import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useOrg } from "../../app/context";
import { api } from "../../lib/api/client";
import { pollInterval } from "../../lib/polling";
import type { Source, SourceVersion } from "../../lib/types";
import {
  ActionForm,
  ErrorNotice,
  Field,
  More,
  Status,
  usePages,
} from "../../components/ui";
export function Sources() {
  const org = useOrg();
  const path = `/organisations/${org.org_id}/sources`;
  const list = usePages<Source>(path);
  const [selected, setSelected] = useState<Source | null>(null);
  if (!org.roles.includes("admin")) return <h1>Access denied</h1>;
  return (
    <>
      <p className="eyebrow">Approved organisation knowledge</p>
      <h1>Source library</h1>
      <p>
        Text guidance becomes review evidence only after ingestion and your
        approval.
      </p>
      <div className="columns">
        <section>
          <ErrorNotice error={list.error} />
          {list.isPending && <p>Loading sources…</p>}
          {list.data?.pages
            .flatMap((p) => p.items)
            .map((s) => (
              <article className="card" key={s.source_id}>
                <h2>{s.title}</h2>
                <p>{s.category}</p>
                <Status value={s.ingestion_status} />
                <Status value={s.approval_status} />
                {s.ingestion_error_code && (
                  <p className="error">
                    Ingestion failed: {s.ingestion_error_code}
                  </p>
                )}
                <button className="secondary" onClick={() => setSelected(s)}>
                  Inspect latest version
                </button>
              </article>
            ))}
          {list.data?.pages[0].items.length === 0 && (
            <p>No guidance uploaded yet.</p>
          )}
          <More {...list} />
          {selected && selected.latest_version_id && (
            <Version
              source={selected}
              path={path}
              onClose={() => setSelected(null)}
            />
          )}
        </section>
        <section className="card">
          <h2>Upload guidance</h2>
          <p>.txt or .md · up to 2 MiB</p>
          <ActionForm
            label="Upload for processing"
            onSubmit={async (f) => {
              const file = f.get("file");
              if (!(file instanceof File) || !file.name.match(/\.(txt|md)$/i))
                throw new Error("Choose a .txt or .md file.");
              if (file.size > 2 * 1024 * 1024)
                throw new Error("The file must be smaller than 2 MiB.");
              const from = String(f.get("effective_from"));
              const to = String(f.get("effective_to"));
              if (to && to <= from)
                throw new Error("End date must be later than start date.");
              if (!to) f.delete("effective_to");
              const sourceId = String(f.get("source_id") ?? "");
              f.delete("source_id");
              await api(
                sourceId ? `${path}/${sourceId}/versions` : path,
                "POST",
                f,
                crypto.randomUUID(),
              );
              setSelected(null);
            }}
          >
            <Field label="Upload destination">
              <select name="source_id">
                <option value="">New source</option>
                {list.data?.pages
                  .flatMap((p) => p.items)
                  .map((s) => (
                    <option key={s.source_id} value={s.source_id}>
                      New version of {s.title}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label="Title">
              <input name="title" required maxLength={240} />
            </Field>
            <Field label="Category">
              <select name="category">
                {["guidelines", "facts", "terminology", "messaging"].map(
                  (c) => (
                    <option key={c}>{c}</option>
                  ),
                )}
              </select>
            </Field>
            <Field label="Effective from">
              <input type="date" required name="effective_from" />
            </Field>
            <Field label="Effective to (optional)">
              <input type="date" name="effective_to" />
            </Field>
            <Field label="Guidance file">
              <input type="file" required name="file" accept=".txt,.md" />
            </Field>
          </ActionForm>
        </section>
      </div>
    </>
  );
}
function Version({
  source,
  path,
  onClose,
}: {
  source: Source;
  path: string;
  onClose: () => void;
}) {
  const org = useOrg();
  const client = useQueryClient();
  const versionPath = `${path}/${source.source_id}/versions/${source.latest_version_id}`;
  const detail = useQuery({
    queryKey: ["version", versionPath],
    queryFn: () => api<SourceVersion>(versionPath),
    refetchInterval: (q) =>
      pollInterval(
        ["queued", "processing"].includes(q.state.data?.ingestion_status ?? ""),
        q.state.dataUpdateCount,
        q.state.error,
      ),
  });
  const v = detail.data;
  return (
    <section className="card">
      <button className="secondary" onClick={onClose}>
        Close version
      </button>
      <h2>{source.title}</h2>
      <ErrorNotice error={detail.error} />
      {v && (
        <>
          <p>
            Version {v.version_label} · {v.passage_count} passages
          </p>
          <Status value={v.ingestion_status} />
          <Status value={v.approval_status} />
          <p>
            Effective {v.effective_from}
            {v.effective_to ? ` to ${v.effective_to}` : ""}
          </p>
          {v.original_available && (
            <a href={`/api/v1${versionPath}/original`} download>
              Download original
            </a>
          )}
          <ActionForm
            label="Approve this version"
            disabled={
              v.ingestion_status !== "ready" || v.approval_status !== "pending"
            }
            onSubmit={async () => {
              try {
                await api(`${versionPath}/approve`, "POST", {
                  expected_corpus_version: org.corpus_version,
                });
              } finally {
                await client.invalidateQueries({ queryKey: ["organisations"] });
              }
            }}
          />
          <ActionForm
            label="Archive source"
            disabled={v.approval_status !== "approved"}
            onSubmit={async () => {
              try {
                await api(`${path}/${source.source_id}/archive`, "POST", {
                  expected_corpus_version: org.corpus_version,
                });
              } finally {
                await client.invalidateQueries({ queryKey: ["organisations"] });
              }
            }}
          />
        </>
      )}
    </section>
  );
}
