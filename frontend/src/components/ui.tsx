import { useState, type ReactNode } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { api, ApiError, errorMessage } from "../lib/api/client";
import { pollInterval } from "../lib/polling";
import type { Page } from "../lib/types";
export function ErrorNotice({ error }: { error: unknown }) {
  const client = useQueryClient();
  return error ? (
    <div className="error" role="alert">
      <p>{errorMessage(error)}</p>
      {error instanceof ApiError && error.status === 401 ? (
        <a href="/login">Sign in again</a>
      ) : (
        <button
          type="button"
          className="secondary"
          onClick={() => void client.invalidateQueries()}
        >
          Refresh server state
        </button>
      )}
    </div>
  ) : null;
}
export function Status({ value }: { value: string | null | undefined }) {
  return (
    <span className={`status ${value ?? ""}`}>
      {value?.replaceAll("_", " ") ?? "Pending"}
    </span>
  );
}
export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}
export function ActionForm({
  children,
  onSubmit,
  label = "Save",
  disabled = false,
}: {
  children?: ReactNode;
  onSubmit: (data: FormData) => Promise<unknown>;
  label?: string;
  disabled?: boolean;
}) {
  const client = useQueryClient();
  const action = useMutation({
    mutationFn: onSubmit,
    onSuccess: () => {
      void client.invalidateQueries();
    },
  });
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        action.mutate(new FormData(e.currentTarget));
      }}
    >
      {children}
      <ErrorNotice error={action.error} />
      <button disabled={disabled || action.isPending}>
        {action.isPending ? "Working…" : label}
      </button>
      {action.isSuccess && <span role="status"> Saved</span>}
    </form>
  );
}
export function usePages<T>(path: string, enabled = true) {
  return useInfiniteQuery({
    queryKey: ["page", path],
    enabled,
    initialPageParam: "",
    queryFn: ({ pageParam }) =>
      api<Page<T>>(
        `${path}${path.includes("?") ? "&" : "?"}limit=50${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ""}`,
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: (q) =>
      pollInterval(
        q.state.data?.pages.some((p) =>
          p.items.some((item) => {
            const row = item as Record<string, unknown>;
            return ["queued", "processing"].includes(
              String(row.state ?? row.ingestion_status),
            );
          }),
        ) ?? false,
        q.state.dataUpdateCount,
        q.state.error,
      ),
  });
}
export function More({
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
}: {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => unknown;
}) {
  return hasNextPage ? (
    <button
      className="secondary"
      disabled={isFetchingNextPage}
      onClick={() => fetchNextPage()}
    >
      Load more
    </button>
  ) : null;
}
export function TextAction({
  label,
  max = 4000,
  onSubmit,
}: {
  label: string;
  max?: number;
  onSubmit: (value: string) => Promise<unknown>;
}) {
  const [value, setValue] = useState("");
  return (
    <ActionForm
      label={label}
      onSubmit={async () => {
        if (!value.trim() || Array.from(value).length > max)
          throw new Error(`Enter between 1 and ${max} characters.`);
        await onSubmit(value);
        setValue("");
      }}
    >
      <Field label={label}>
        <textarea
          required
          value={value}
          onChange={(e) => setValue(e.target.value)}
          rows={3}
        />
      </Field>
      <small>
        {Array.from(value).length}/{max} characters
      </small>
    </ActionForm>
  );
}
