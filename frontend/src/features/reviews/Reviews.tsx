import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useOrg, useSession } from "../../app/context";
import { api, ApiError } from "../../lib/api/client";
import { pollInterval } from "../../lib/polling";
import { anchorMatches, applySuggestion, codepoints } from "../../lib/anchors";
import type {
  Comment,
  Evidence,
  Question,
  Review,
  Revision,
} from "../../lib/types";
import {
  ActionForm,
  ErrorNotice,
  Field,
  More,
  Status,
  TextAction,
  usePages,
} from "../../components/ui";
export function Citation({ citation }: { citation: Evidence }) {
  const org = useOrg();
  const [open, setOpen] = useState(false);
  const detail = useQuery({
    queryKey: ["evidence", org.org_id, citation.passage_id],
    queryFn: () =>
      api<Evidence>(
        `/organisations/${org.org_id}/evidence/${citation.passage_id}`,
      ),
    enabled: open,
  });
  return (
    <div className="citation">
      <button className="link" onClick={() => setOpen(!open)}>
        {citation.title} · v{citation.source_version} · {citation.source_ref}
      </button>
      <Status value={citation.current_status} />
      {open && (
        <>
          <ErrorNotice error={detail.error} />
          <blockquote>{detail.data?.excerpt ?? citation.excerpt}</blockquote>
          <small>
            Effective {detail.data?.effective_from ?? citation.effective_from} ·{" "}
            {citation.source_id}
          </small>
        </>
      )}
    </div>
  );
}

export function ChatHistory({ turns }: { turns: Question[] }) {
  const threadRef = useRef<HTMLDivElement>(null);
  const previousCount = useRef(turns.length);
  useEffect(() => {
    if (turns.length > previousCount.current && threadRef.current) {
      threadRef.current.scrollTo({ top: threadRef.current.scrollHeight, behavior: "smooth" });
    }
    previousCount.current = turns.length;
  }, [turns.length]);
  if (turns.length === 0)
    return (
      <div className="chat-empty">
        <strong>Start a conversation about this review</strong>
        <p>Ask why a passage was cited, clarify a finding, or request another grounded edit.</p>
      </div>
    );
  return (
    <div className="chat-thread" aria-label="Reviewer AI conversation" aria-live="polite" ref={threadRef}>
      {turns.map((turn) => (
        <article className="chat-turn" key={turn.turn_id}>
          <div className="chat-message chat-user">
            <span className="chat-speaker">Reviewer</span>
            <p>{turn.question}</p>
          </div>
          <div className="chat-message chat-assistant">
            <span className="chat-speaker">VeriLoom AI</span>
            {turn.state === "completed" ? (
              <>
                {turn.answer_status && (
                  <p className="chat-status"><Status value={turn.answer_status} /></p>
                )}
                <p>{turn.answer}</p>
                {turn.citations.length > 0 && (
                  <div className="chat-citations">
                    <strong>Approved sources</strong>
                    {turn.citations.map((citation) => (
                      <Citation key={citation.passage_id} citation={citation} />
                    ))}
                  </div>
                )}
                {turn.route && <small className="chat-meta">Evidence route: {turn.route.replaceAll("_", " ")}</small>}
              </>
            ) : turn.state === "failed" ? (
              <p className="chat-error" role="alert">This answer failed. You can ask again while the review is open.</p>
            ) : (
              <p className="chat-status" role="status">Searching approved guidance and preparing an answer…</p>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}

export function suggestedQuestions(
  selected?: Review["findings"][number],
  coverage?: Review["coverage"],
) {
  return selected
    ? ["Why was this finding flagged?", "Which source supports this finding?", ...(selected.suggested_edit ? ["Suggest a different edit grounded in approved guidance."] : [])]
    : ["Which approved guidance applies to this draft?", ...(coverage === "partial" || coverage === "insufficient" ? [`Why is evidence coverage ${coverage}?`] : [])];
}

export function ChatComposer({
  path,
  revisionId,
  findings,
  coverage,
}: {
  path: string;
  revisionId: string;
  findings: Review["findings"];
  coverage: Review["coverage"];
}) {
  const client = useQueryClient();
  const [findingId, setFindingId] = useState("");
  const [question, setQuestion] = useState("");
  const [validation, setValidation] = useState("");
  const pendingKey = useRef<{ question: string; findingId: string; key: string } | null>(null);
  const selected = findings.find((f) => f.finding_id === findingId);
  const action = useMutation({
    mutationFn: async () => {
      const trimmed = question.trim();
      if (!trimmed || Array.from(trimmed).length > 2000)
        throw new Error("Enter a question between 1 and 2,000 characters.");
      const prior = pendingKey.current;
      const key = prior?.question === trimmed && prior.findingId === findingId
        ? prior.key : crypto.randomUUID();
      pendingKey.current = { question: trimmed, findingId, key };
      return api(`${path}/questions`, "POST", {
        revision_id: revisionId,
        ...(findingId ? { finding_id: findingId } : {}),
        question: trimmed,
      }, key);
    },
    onSuccess: async () => {
      setQuestion("");
      setValidation("");
      pendingKey.current = null;
      await client.invalidateQueries({ queryKey: ["page", `${path}/questions`] });
    },
  });
  const prompts = suggestedQuestions(selected, coverage);
  return (
    <form className="chat-composer" onSubmit={(event) => {
      event.preventDefault();
      const length = Array.from(question.trim()).length;
      if (length < 1 || length > 2000) {
        setValidation("Enter a question between 1 and 2,000 characters.");
        return;
      }
      setValidation("");
      action.mutate();
    }}>
      <label className="chat-context">
        <span>Ask about</span>
        <select value={findingId} onChange={(event) => setFindingId(event.target.value)}>
          <option value="">Whole review</option>
          {findings.map((finding) => (
            <option key={finding.finding_id} value={finding.finding_id}>
              {finding.category}: {finding.explanation.slice(0, 60)}
            </option>
          ))}
        </select>
      </label>
      <div className="chat-suggestion-prompts" aria-label="Suggested questions">
        {prompts.map((prompt) => (
          <button className="secondary" type="button" key={prompt} onClick={() => setQuestion(prompt)}>
            {prompt}
          </button>
        ))}
      </div>
      <label className="chat-question">
        <span>Your question</span>
        <textarea
          value={question}
          onChange={(event) => { setQuestion(event.target.value); setValidation(""); }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          rows={3}
          placeholder="Ask about a finding, source, or a safer edit…"
          aria-describedby="chat-composer-help"
          disabled={action.isPending}
        />
      </label>
      <div className="chat-composer-footer">
        <small id="chat-composer-help">Enter to send · Shift+Enter for a new line · {Array.from(question).length}/2,000</small>
        <button type="submit" disabled={action.isPending || !question.trim()}>
          {action.isPending ? "Sending…" : "Send question"}
        </button>
      </div>
      {validation && <p className="chat-error" role="alert">{validation}</p>}
      <ErrorNotice error={action.error} />
      <small className="chat-meta">Answers use current approved guidance. Suggested edits are proposals; only the Creator changes the draft.</small>
    </form>
  );
}
export function ReviewWorkspace({
  reviewId: provided,
  working,
}: {
  reviewId?: string;
  working?: { content: string; onApply: (content: string) => void };
}) {
  const params = useParams();
  const reviewId = provided ?? params.reviewId;
  const org = useOrg();
  const session = useSession();
  const client = useQueryClient();
  const path = `/organisations/${org.org_id}/reviews/${reviewId}`;
  const review = useQuery({
    queryKey: ["review", org.org_id, reviewId],
    queryFn: () => api<Review>(path),
    enabled: !!reviewId,
    refetchInterval: (q) =>
      pollInterval(
        ["queued", "processing"].includes(q.state.data?.state ?? ""),
        q.state.dataUpdateCount,
        q.state.error,
      ),
  });
  const r = review.data;
  const revision = useQuery({
    queryKey: ["revision", org.org_id, r?.draft_id, r?.revision_number],
    queryFn: () =>
      api<Revision>(
        `/organisations/${org.org_id}/drafts/${r!.draft_id}/revisions/${r!.revision_number}`,
      ),
    enabled: !!r,
  });
  const comments = usePages<Comment>(`${path}/comments`);
  const questions = usePages<Question>(`${path}/questions`);
  const [action, setAction] = useState("request_changes");
  const assigned =
    r?.assigned_reviewer_user_id === session.user_id &&
    org.roles.includes("reviewer");
  const active = r?.state === "ready_for_review";
  const override =
    r?.coverage === "insufficient" ||
    r?.findings.some((f) => f.severity === "high");
  const points = codepoints(revision.data?.content ?? "");
  if (!provided && !org.roles.includes("reviewer"))
    return <h1>Access denied</h1>;
  return (
    <section className="review">
      <ErrorNotice error={review.error} />
      {review.isPending && <p role="status">Loading review…</p>}
      {r && (
        <>
          <header className="row">
            <div>
              <p className="eyebrow">
                Immutable submitted revision {r.revision_number}
              </p>
              <h2>{revision.data?.title ?? "Review workspace"}</h2>
            </div>
            <Status value={r.state} />
          </header>
          <p role="status" aria-live="polite">
            {["queued", "processing"].includes(r.state)
              ? "Review in progress. This page updates automatically."
              : r.state === "failed"
                ? "Automated review failed."
                : `Evidence coverage: ${r.coverage ?? "pending"}`}
          </p>
          {r.decision && (
            <section className="notice">
              <h3>Human decision: {r.decision.action.replaceAll("_", " ")}</h3>
              {r.decision.comment && <p>{r.decision.comment}</p>}
              {r.decision.override_reason && (
                <p>
                  <strong>Override reason:</strong> {r.decision.override_reason}
                </p>
              )}
              <small>
                Reviewer {r.decision.decided_by_user_id.slice(0, 8)} ·{" "}
                {new Date(r.decision.decided_at).toLocaleString()}
              </small>
            </section>
          )}
          {r.coverage_reason && (
            <section className="notice">
              <strong>
                {r.coverage === "insufficient"
                  ? "Needs human review"
                  : "Coverage warning"}
              </strong>
              <p>{r.coverage_reason}</p>
            </section>
          )}
          {r.failure && (
            <section className="error">
              <p>{r.failure.message}</p>
              <ActionForm
                label="Retry failed review"
                onSubmit={() =>
                  api(`${path}/retry`, "POST", undefined, crypto.randomUUID())
                }
              />
            </section>
          )}
          <ErrorNotice error={revision.error} />
          <details open>
            <summary>Read submitted text</summary>
            <pre className="submitted">
              {revision.data?.content ?? "Loading revision…"}
            </pre>
          </details>
          <h3>Findings ({r.findings.length})</h3>
          {r.findings.length === 0 && r.coverage === "sufficient" && (
            <p>No findings were returned. Human approval is still required.</p>
          )}
          {r.findings.map((f) => (
            <article className="card finding" key={f.finding_id}>
              <div className="row">
                <strong>{f.category}</strong>
                <Status value={f.severity} />
              </div>
              <p>
                <Status value={f.evidence_status} />
              </p>
              {f.anchor && (
                <blockquote>
                  {revision.data &&
                  anchorMatches(revision.data.content, f.anchor) ? (
                    <mark>
                      {points
                        .slice(f.anchor.start_codepoint, f.anchor.end_codepoint)
                        .join("")}
                    </mark>
                  ) : (
                    f.anchor.quote
                  )}
                </blockquote>
              )}
              <p>{f.explanation}</p>
              {f.suggested_edit && (
                <section>
                  <strong>Suggested edit</strong>
                  <pre>{f.suggested_edit}</pre>
                  {working && (
                    <button
                      className="secondary"
                      disabled={
                        applySuggestion(
                          working.content,
                          revision.data?.content ?? "",
                          f.anchor,
                          f.suggested_edit,
                        ) === null
                      }
                      onClick={() => {
                        const next = applySuggestion(
                          working.content,
                          revision.data?.content ?? "",
                          f.anchor,
                          f.suggested_edit!,
                        );
                        if (next !== null) working.onApply(next);
                      }}
                    >
                      Apply to working copy
                    </button>
                  )}
                  {working && (
                    <small>
                      Save the working copy after applying. If the copy changed
                      or a full sentence targets only a phrase, edit manually.
                    </small>
                  )}
                </section>
              )}
              {f.citations.map((c) => (
                <Citation key={c.passage_id} citation={c} />
              ))}
            </article>
          ))}
          <div className="chat-review-grid">
            <section className="chat-panel" aria-label="Review conversation">
              <div className="chat-head">
                <div>
                  <p className="eyebrow">Grounded conversation</p>
                  <h3>Ask VeriLoom AI</h3>
                  <p>Explore findings and refine suggestions with approved source evidence. Recent turns help resolve follow-ups; they are never treated as brand rules.</p>
                </div>
                <span className="chat-status"><Status value={active ? "ready_for_review" : r.state} /></span>
              </div>
              {r.state === "approved" && (
                <p className="chat-meta">This revision is approved and read-only. Earlier answers remain below. Ask new questions on a review that is ready for review.</p>
              )}
              {r.state === "changes_requested" && (
                <p className="chat-meta">Questions are closed for this revision. Ask follow-ups after the Creator submits the next revision and its review is ready.</p>
              )}
              <ErrorNotice error={questions.error} />
              {questions.isPending ? (
                <p className="chat-status" role="status">Loading conversation…</p>
              ) : (
                <ChatHistory turns={questions.data?.pages.flatMap((page) => page.items) ?? []} />
              )}
              <More {...questions} />
              {assigned && active && <ChatComposer key={r.revision_id} path={path} revisionId={r.revision_id} findings={r.findings} coverage={r.coverage} />}
              {!assigned && active && <p className="chat-meta">Only the assigned Reviewer can ask questions. The Creator can read this conversation.</p>}
            </section>
            <section className="chat-notes">
              <h3>Comments</h3>
              <ErrorNotice error={comments.error} />
              {comments.data?.pages
                .flatMap((p) => p.items)
                .map((c) => (
                  <article className="card" key={c.comment_id}>
                    <p>{c.body}</p>
                    <small>
                      {c.actor_user_id === session.user_id
                        ? "You"
                        : `Participant ${c.actor_user_id.slice(0, 8)}`}{" "}
                      · {new Date(c.created_at).toLocaleString()}
                    </small>
                  </article>
                ))}
              <More {...comments} />
              {["ready_for_review", "changes_requested"].includes(r.state) && (
                <TextAction
                  label="Add comment"
                  onSubmit={(body) =>
                    api(`${path}/comments`, "POST", {
                      revision_id: r.revision_id,
                      body,
                    })
                  }
                />
              )}
            </section>
          </div>
          {assigned && active && (
            <section className="card decision">
              <h3>Your decision</h3>
              <p>
                You are approving this submitted revision. AI suggestions do not
                change the Creator’s text.
              </p>
              <ActionForm
                label={
                  action === "approve" ? "Approve revision" : "Request changes"
                }
                onSubmit={async (f) => {
                  const comment = String(f.get("comment") ?? "").trim();
                  const reason = String(f.get("override_reason") ?? "").trim();
                  if (action === "request_changes" && !comment)
                    throw new Error("Explain the changes you need.");
                  if (
                    action === "approve" &&
                    override &&
                    Array.from(reason).length < 15
                  )
                    throw new Error(
                      "Provide an override reason of at least 15 characters.",
                    );
                  if (
                    Array.from(comment).length > 4000 ||
                    Array.from(reason).length > 4000
                  )
                    throw new Error(
                      "Comments and reasons must be at most 4,000 characters.",
                    );
                  try {
                    await api(
                      `${path}/decisions`,
                      "POST",
                      {
                        expected_revision_id: r.revision_id,
                        expected_review_version: r.review_version,
                        action,
                        comment: comment || null,
                        override_reason: reason || null,
                      },
                      crypto.randomUUID(),
                    );
                  } catch (e) {
                    if (e instanceof ApiError && e.status === 409)
                      await client.invalidateQueries({
                        queryKey: ["review", org.org_id, reviewId],
                      });
                    throw e;
                  }
                }}
              >
                <Field label="Decision">
                  <select
                    value={action}
                    onChange={(e) => setAction(e.target.value)}
                  >
                    <option value="request_changes">Request changes</option>
                    <option value="approve">Approve</option>
                  </select>
                </Field>
                <Field
                  label={
                    action === "request_changes"
                      ? "Required change request"
                      : "Comment (optional)"
                  }
                >
                  <textarea
                    name="comment"
                    required={action === "request_changes"}
                    rows={3}
                  />
                </Field>
                {action === "approve" && override && (
                  <>
                    <p className="notice">
                      High-severity findings or insufficient coverage require a
                      recorded human override.
                    </p>
                    <Field label="Override reason (at least 15 characters)">
                      <textarea name="override_reason" required rows={3} />
                    </Field>
                  </>
                )}
              </ActionForm>
            </section>
          )}
        </>
      )}
    </section>
  );
}
