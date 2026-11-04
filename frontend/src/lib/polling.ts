import { ApiError } from "./api/client";
/** Pending resource polling stops at terminal states and respects server throttling. */
export function pollInterval(
  pending: boolean,
  updates: number,
  error: unknown,
): number | false {
  if (error instanceof ApiError && error.status === 429)
    return Math.max(1000, (error.retryAfter ?? 30) * 1000);
  if (error instanceof ApiError && [401, 403, 404].includes(error.status))
    return false;
  return pending
    ? Math.min(2000 * (1 + Math.floor(updates / 5)), 10000)
    : false;
}
