import { describe, it, expect } from "vitest";
import { anchorMatches, applySuggestion } from "./anchors";
describe("immutable NFC codepoint anchors", () => {
  it("counts astral characters as one codepoint", () => {
    const anchor = { start_codepoint: 2, end_codepoint: 6, quote: "best" };
    expect(anchorMatches("😀 best café", anchor)).toBe(true);
    expect(
      applySuggestion("😀 best café", "😀 best café", anchor, "local"),
    ).toBe("😀 local café");
  });
  it("normalizes combining sequences", () => {
    expect(
      anchorMatches("cafe\u0301", {
        start_codepoint: 0,
        end_codepoint: 4,
        quote: "café",
      }),
    ).toBe(true);
  });
  it("rejects stale copies, wrong quotes and invalid ranges", () => {
    const a = { start_codepoint: 0, end_codepoint: 4, quote: "best" };
    expect(applySuggestion("best new", "best", a, "local")).toBeNull();
    expect(anchorMatches("best", { ...a, quote: "good" })).toBe(false);
    expect(anchorMatches("best", { ...a, start_codepoint: -1 })).toBe(false);
    expect(applySuggestion("best", "best", null, "local")).toBeNull();
  });
  it("requires manual editing when a sentence suggestion targets only a phrase", () => {
    const submitted = "Our coffee is the best.";
    const phrase = { start_codepoint: 14, end_codepoint: 22, quote: "the best" };
    expect(
      applySuggestion(submitted, submitted, phrase, "Our coffee is freshly roasted."),
    ).toBeNull();
    const sentence = {
      start_codepoint: 0,
      end_codepoint: submitted.length,
      quote: submitted,
    };
    expect(
      applySuggestion(submitted, submitted, sentence, "Our coffee is freshly roasted."),
    ).toBe("Our coffee is freshly roasted.");
  });
});
