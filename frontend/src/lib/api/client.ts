let csrfToken = "";
export const setCsrfToken = (token: string) => {
  csrfToken = token;
};
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
    public requestId = "",
    public retryAfter?: number,
  ) {
    super(message);
  }
}
export function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError))
    return error instanceof Error
      ? error.message
      : "Something went wrong. Try again.";
  const mapped: Record<number, string> = {
    401: "Your session ended. Sign in again.",
    403: "You do not have access to this action.",
    404: "This resource was not found.",
    409: "The saved state changed. Refresh and review before trying again.",
    413: "Choose a file smaller than 2 MiB.",
    415: "Choose a .txt or .md file.",
    429: `Too many requests. Retry in ${error.retryAfter ?? 30} seconds.`,
    502: "The service is temporarily unavailable. Your unsaved text is still here.",
    503: "The service is temporarily unavailable. Your unsaved text is still here.",
    504: "The request timed out. Refresh the resource before retrying.",
  };
  return `${mapped[error.status] ?? error.message}${error.requestId ? ` (Request ${error.requestId})` : ""}`;
}
const base = import.meta.env.VITE_API_BASE ?? "/api/v1";
if (!/^\/api\/v1\/?$/.test(base))
  throw new Error("VITE_API_BASE must be same-origin /api/v1");
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  key?: string,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined && !(body instanceof FormData))
    headers["Content-Type"] = "application/json";
  if (!["GET", "HEAD"].includes(method) && csrfToken)
    headers["X-CSRF-Token"] = csrfToken;
  if (key) headers["Idempotency-Key"] = key;
  let response: Response;
  try {
    response = await fetch(`${base.replace(/\/$/, "")}${path}`, {
      method,
      credentials: "include",
      headers,
      body:
        body === undefined
          ? undefined
          : body instanceof FormData
            ? body
            : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(
      0,
      "network_error",
      "Could not connect. Check your connection, then refresh the resource before retrying.",
    );
  }
  const result = await response.json().catch(() => null);
  if (!response.ok) {
    const e = result?.error;
    const retry = response.headers.get("Retry-After");
    const seconds =
      retry === null
        ? undefined
        : /^\d+$/.test(retry)
          ? Number(retry)
          : Math.ceil((Date.parse(retry) - Date.now()) / 1000);
    throw new ApiError(
      response.status,
      e?.code ?? "http_error",
      e?.message ?? "The request could not be completed.",
      e?.details ?? {},
      response.headers.get("X-Request-ID") ?? e?.request_id ?? "",
      seconds !== undefined && Number.isFinite(seconds)
        ? Math.max(0, seconds)
        : undefined,
    );
  }
  return result as T;
}
