import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";
import { AssignedDraft } from "./Drafts";

const state = vi.hoisted(() => ({
  query: { data: undefined as { review_id: string | null } | undefined, error: null as Error | null, isPending: true },
}));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router-dom")>()),
  useParams: () => ({ reviewId: "draft-1" }),
}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-query")>()),
  useQuery: () => state.query,
}));
vi.mock("../../app/context", () => ({
  useOrg: () => ({ org_id: "org-1", roles: ["reviewer"] }),
}));
vi.mock("../../components/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../components/ui")>()),
  ErrorNotice: ({ error }: { error: Error | null }) =>
    error ? <p role="alert">{error.message}</p> : null,
}));
vi.mock("../reviews/Reviews", () => ({
  ReviewWorkspace: ({ reviewId }: { reviewId: string }) => <p>{reviewId}</p>,
}));

beforeEach(() => {
  state.query = { data: undefined, error: null, isPending: true };
});

it("shows loading only while the assigned draft is pending", () => {
  expect(renderToStaticMarkup(<AssignedDraft />)).toContain("Loading assigned review");
  state.query = { data: { review_id: null }, error: null, isPending: false };
  const ready = renderToStaticMarkup(<AssignedDraft />);
  expect(ready).toContain("no submitted review yet");
  expect(ready).not.toContain("Loading assigned review");
});

it("shows the fetch error and does not leave an endless spinner", () => {
  state.query = { data: undefined, error: new Error("Draft unavailable"), isPending: false };
  const failed = renderToStaticMarkup(<AssignedDraft />);
  expect(failed).toContain("Draft unavailable");
  expect(failed).not.toContain("Loading assigned review");
});

it("opens the persisted review when the draft includes its review ID", () => {
  state.query = { data: { review_id: "review-1" }, error: null, isPending: false };
  expect(renderToStaticMarkup(<AssignedDraft />)).toContain("review-1");
});
