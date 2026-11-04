import type { Anchor } from "./types";
export const codepoints = (text: string) => Array.from(text.normalize("NFC"));
export function anchorMatches(text: string, anchor: Anchor): boolean {
  const points = codepoints(text);
  return (
    Number.isInteger(anchor.start_codepoint) &&
    Number.isInteger(anchor.end_codepoint) &&
    anchor.start_codepoint >= 0 &&
    anchor.end_codepoint > anchor.start_codepoint &&
    anchor.end_codepoint <= points.length &&
    points.slice(anchor.start_codepoint, anchor.end_codepoint).join("") ===
      anchor.quote
  );
}
export function applySuggestion(
  working: string,
  submitted: string,
  anchor: Anchor | null,
  replacement: string,
): string | null {
  if (
    !anchor ||
    working.normalize("NFC") !== submitted.normalize("NFC") ||
    !anchorMatches(submitted, anchor)
  )
    return null;
  const points = codepoints(working);
  return [
    ...points.slice(0, anchor.start_codepoint),
    replacement,
    ...points.slice(anchor.end_codepoint),
  ].join("");
}
