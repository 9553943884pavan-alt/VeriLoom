import { useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
  const [finding, setFinding] = useState("");
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
          <div className="columns">
            <section>
              <h3>Reviewer follow-up</h3>
              {r.state === "approved" && (
                <p>This revision is approved and read-only. Ask new AI questions on a review that is ready for review, before making a decision.</p>
              )}
              {r.state === "changes_requested" && (
                <p>Questions are closed for this revision. Ask follow-ups after the Creator submits the next revision and its review is ready.</p>
              )}
              <ErrorNotice error={questions.error} />
              {questions.data?.pages
                .flatMap((p) => p.items)
                .map((q) => (
                  <article className="card" key={q.turn_id}>
                    <strong>{q.question}</strong>
                    <p>
                      <Status value={q.state} />
                      {q.answer_status && <Status value={q.answer_status} />}
                    </p>
                    {q.state === "completed" && (
                      <>
                        <p>{q.answer}</p>
                        <small>Evidence route: {q.route}</small>
                        {q.citations.map((c) => (
                          <Citation key={c.passage_id} citation={c} />
                        ))}
                      </>
                    )}
                    {q.state === "failed" && (
                      <p className="error">
                        The follow-up failed. Ask again when the service is
                        available.
                      </p>
                    )}
                  </article>
                ))}
              <More {...questions} />
              {assigned && active && (
                <>
                  <Field label="Question context">
                    <select
                      value={finding}
                      onChange={(e) => setFinding(e.target.value)}
                    >
                      <option value="">Whole review</option>
                      {r.findings.map((f) => (
                        <option key={f.finding_id} value={f.finding_id}>
                          {f.category}: {f.explanation.slice(0, 60)}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <TextAction
                    label="Ask a follow-up"
                    max={2000}
                    onSubmit={(question) =>
                      api(
                        `${path}/questions`,
                        "POST",
                        {
                          revision_id: r.revision_id,
                          ...(finding ? { finding_id: finding } : {}),
                          question,
                        },
                        crypto.randomUUID(),
                      )
                    }
                  />
                </>
              )}
            </section>
            <section>
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
