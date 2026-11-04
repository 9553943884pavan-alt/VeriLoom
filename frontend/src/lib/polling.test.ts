import { expect, it } from "vitest";
import { pollInterval } from "./polling";
import { ApiError } from "./api/client";
it("backs off bounded pending polls and stops terminal or unauthorized resources", () => {
  expect(pollInterval(true, 0, null)).toBe(2000);
  expect(pollInterval(true, 100, null)).toBe(10000);
  expect(pollInterval(false, 1, null)).toBe(false);
  expect(pollInterval(true, 1, new ApiError(401, "expired", "Expired"))).toBe(
    false,
  );
});
it("honors the server retry window", () => {
  expect(
    pollInterval(true, 1, new ApiError(429, "limited", "Retry", {}, "", 45)),
  ).toBe(45000);
});
