import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useOrg } from "../../app/context";
import { api, ApiError } from "../../lib/api/client";
import { pollInterval } from "../../lib/polling";
import type { Draft, Revision, Reviewer } from "../../lib/types";
import {
  ErrorNotice,
  Field,
  More,
  Status,
  usePages,
} from "../../components/ui";
import { ReviewWorkspace } from "../reviews/Reviews";
export function DraftList({ assigned = false }: { assigned?: boolean }) {
  const org = useOrg();
  const role = assigned ? "reviewer" : "creator";
  const list = usePages<Draft>(
    `/organisations/${org.org_id}/drafts?view=${assigned ? "assigned" : "mine"}`,
  );
  if (!org.roles.includes(role)) return <h1>Access denied</h1>;
  return (
    <>
      <p className="eyebrow">
        {assigned ? "Your assigned submissions" : "Your writing workspace"}
      </p>
      <h1>{assigned ? "Review inbox" : "My drafts"}</h1>
      {!assigned && (
        <Link className="button" to="new">
          New text draft +
        </Link>
      )}
      <ErrorNotice error={list.error} />
      {list.isPending && <p>Loading drafts…</p>}
      {list.data?.pages
        .flatMap((p) => p.items)
        .map((d) => (
          <Link
            className="card row"
            key={d.draft_id}
            to={
              assigned
                ? `/org/${org.org_id}/reviewer/inbox/${d.draft_id}`
                : d.draft_id
            }
          >
            <div>
              <h2>{d.title}</h2>
              <p>{d.channel}</p>
            </div>
            <Status value={d.state} />
          </Link>
        ))}
      {list.data?.pages[0].items.length === 0 && (
        <section className="notice">
          {assigned
            ? "No assigned drafts yet."
            : "No drafts yet. Create your first text draft."}
        </section>
      )}
      <More {...list} />
    </>
  );
}
export function AssignedDraft() {
  const { reviewId: draftId } = useParams();
  const org = useOrg();
  const draft = useQuery({
    queryKey: ["draft", org.org_id, draftId],
    queryFn: () => api<Draft>(`/organisations/${org.org_id}/drafts/${draftId}`),
  });
  if (!org.roles.includes("reviewer")) return <h1>Access denied</h1>;
  return draft.data?.review_id ? (
    <ReviewWorkspace reviewId={draft.data.review_id} />
  ) : (
    <>
      <ErrorNotice error={draft.error} />
      <p>Loading assigned review…</p>
    </>
  );
}
export function DraftEditor() {
  const { draftId } = useParams();
  const org = useOrg();
  const query = useQuery({
    queryKey: ["draft", org.org_id, draftId],
    queryFn: () => api<Draft>(`/organisations/${org.org_id}/drafts/${draftId}`),
    enabled: !!draftId,
    refetchInterval: (q) =>
      pollInterval(
        q.state.data?.state === "processing",
        q.state.dataUpdateCount,
        q.state.error,
      ),
  });
  if (!org.roles.includes("creator")) return <h1>Access denied</h1>;
  if (draftId && !query.data)
    return (
      <>
        <ErrorNotice error={query.error} />
        <p>Loading working copy…</p>
      </>
    );
  return <Editor key={draftId ?? "new"} draft={query.data} />;
}
function Editor({ draft }: { draft?: Draft }) {
  const org = useOrg();
  const navigate = useNavigate();
  const client = useQueryClient();
  const path = `/organisations/${org.org_id}/drafts`;
  const [title, setTitle] = useState(draft?.title ?? "");
  const [channel, setChannel] = useState(draft?.channel ?? "");
  const [content, setContent] = useState(draft?.working_content ?? "");
  const [version, setVersion] = useState(draft?.working_copy_version ?? 1);
  const [reviewer, setReviewer] = useState("");
  const [saved, setSaved] = useState({ title, channel, content });
  const [conflict, setConflict] = useState(false);
  const editable =
    !draft || ["editing", "changes_requested"].includes(draft.state);
  const dirty =
    title !== saved.title ||
    channel !== saved.channel ||
    content !== saved.content;
  const reviewers = usePages<Reviewer>(
    `/organisations/${org.org_id}/reviewers`,
  );
  const revisions = usePages<Revision>(
    `${path}/${draft?.draft_id ?? "new"}/revisions`,
    !!draft,
  );
  const action = useMutation({
    mutationFn: async (kind: "save" | "submit") => {
      if (
        !title.trim() ||
        !channel.trim() ||
        !content ||
        Array.from(content).length > 20000
      )
        throw new Error(
          "Enter a title, channel, and 1–20,000 characters of text.",
        );
      try {
        if (kind === "submit") {
          if (dirty) throw new Error("Save your changes before submitting.");
          if (!reviewer) throw new Error("Choose an eligible reviewer.");
          return await api(
            `${path}/${draft!.draft_id}/submissions`,
            "POST",
            {
              expected_working_copy_version: version,
              reviewer_user_id: reviewer,
            },
            crypto.randomUUID(),
          );
        }
        const result = await api<Draft>(
          draft ? `${path}/${draft.draft_id}` : path,
          draft ? "PATCH" : "POST",
          {
            ...(draft
              ? { expected_working_copy_version: version }
              : { content_format: "text" }),
            title,
            channel,
            content,
          },
        );
        setVersion(result.working_copy_version);
        setSaved({ title, channel, content });
        if (!draft)
          navigate(`/org/${org.org_id}/creator/drafts/${result.draft_id}`);
        return result;
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          setConflict(true);
          await client.invalidateQueries({
            queryKey: ["draft", org.org_id, draft?.draft_id],
          });
        }
        throw e;
      }
    },
    onSuccess: () => {
      void client.invalidateQueries();
    },
  });
  return (
    <>
      <p className="eyebrow">Creator workspace</p>
      <h1>{draft ? "Your working copy" : "Create a text draft"}</h1>
      {draft && (
        <p>
          <Status value={draft.state} /> Saved version {version}
        </p>
      )}
      <section className="card">
        <Field label="Title">
          <input
            required
            maxLength={200}
            disabled={!editable}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </Field>
        <Field label="Channel">
          <input
            required
            maxLength={200}
            disabled={!editable}
            placeholder="e.g. Instagram caption"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
          />
        </Field>
        <Field label="Draft text">
          <textarea
            rows={13}
            disabled={!editable}
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
        </Field>
        <small>
          {Array.from(content).length}/20,000 characters ·{" "}
          {dirty ? "Unsaved changes" : "Saved copy"}
        </small>
        <ErrorNotice error={action.error} />
        {conflict && (
          <p className="error">
            Your text is preserved above. Copy it before reloading to compare
            the latest server copy.{" "}
            <button className="secondary" onClick={() => location.reload()}>
              Reload saved copy
            </button>
          </p>
        )}
        <div className="actions">
          <button
            disabled={!editable || action.isPending || conflict}
            onClick={() => action.mutate("save")}
          >
            {action.isPending ? "Working…" : "Save working copy"}
          </button>
          {draft && editable && (
            <>
              <Field label="Assigned reviewer">
                <select
                  value={reviewer}
                  onChange={(e) => setReviewer(e.target.value)}
                >
                  <option value="">Choose reviewer</option>
                  {reviewers.data?.pages
                    .flatMap((p) => p.items)
                    .map((r) => (
                      <option key={r.user_id} value={r.user_id}>
                        {r.display_name}
                      </option>
                    ))}
                </select>
              </Field>
              <button
                disabled={dirty || !reviewer || action.isPending || conflict}
                onClick={() => action.mutate("submit")}
              >
                Submit saved copy
              </button>
            </>
          )}
        </div>
        <ErrorNotice error={reviewers.error} />
        {reviewers.data?.pages[0].items.length === 0 && (
          <p>No eligible reviewers. Ask an admin to invite a reviewer.</p>
        )}
        <p className="disclosure">
          When submitted, selected draft text and approved source passages are
          sent by the backend to Sarvam AI, with the configured IndieRouter
          fallback after transient retry exhaustion. Human reviewers decide
          approval.
        </p>
      </section>
      {draft && (
        <>
          <h2>Submitted revisions</h2>
          <ErrorNotice error={revisions.error} />
          {revisions.data?.pages
            .flatMap((p) => p.items)
            .map((r) => (
              <details className="card" key={r.revision_id}>
                <summary>
                  Revision {r.revision_number} · {r.title}
                </summary>
                <ReviewWorkspace
                  reviewId={r.review_id}
                  working={
                    editable
                      ? {
                          content,
                          onApply: (text) => {
                            setContent(text);
                          },
                        }
                      : undefined
                  }
                />
              </details>
            ))}
          <More {...revisions} />
          {draft.review_id && draft.state === "processing" && (
            <ReviewWorkspace reviewId={draft.review_id} />
          )}
        </>
      )}
    </>
  );
}
