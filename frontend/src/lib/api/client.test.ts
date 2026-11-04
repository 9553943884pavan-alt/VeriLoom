import { afterEach, describe, it, expect, vi } from "vitest";
import { api, ApiError, errorMessage, setCsrfToken } from "./client";
afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken("");
});
describe("API security and errors", () => {
  it("sends same-origin cookies, CSRF, idempotency and exact JSON wire fields", async () => {
    setCsrfToken("csrf");
    const mock = vi
      .fn()
      .mockResolvedValue(new Response('{"state":"queued"}', { status: 202 }));
    vi.stubGlobal("fetch", mock);
    await api("/test", "POST", { revision_id: "revision" }, "key");
    expect(mock).toHaveBeenCalledWith(
      "/api/v1/test",
      expect.objectContaining({
        credentials: "include",
        body: '{"revision_id":"revision"}',
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": "csrf",
          "Idempotency-Key": "key",
        },
      }),
    );
  });
  it("does not set a multipart boundary", async () => {
    const mock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", mock);
    await api("/sources", "POST", new FormData());
    expect(mock.mock.calls[0][1].headers).not.toHaveProperty("Content-Type");
  });
  it("parses conflict details and request ID", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({
              error: {
                code: "stale_version",
                message: "Changed",
                details: { current_working_copy_version: 2 },
              },
            }),
            { status: 409, headers: { "X-Request-ID": "request-1" } },
          ),
        ),
    );
    await expect(api("/draft")).rejects.toMatchObject({
      status: 409,
      code: "stale_version",
      details: { current_working_copy_version: 2 },
      requestId: "request-1",
    });
  });
  it.each([401, 403, 404, 409, 413, 415, 429, 502, 503, 504])(
    "maps status %i to safe actionable text",
    (status) => {
      expect(
        errorMessage(
          new ApiError(status, "code", "raw internal message", {}, "id", 12),
        ),
      ).not.toContain("raw internal");
    },
  );
  it("honors Retry-After", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("{}", { status: 429, headers: { "Retry-After": "15" } }),
        ),
    );
    await expect(api("/test")).rejects.toMatchObject({ retryAfter: 15 });
  });
});
