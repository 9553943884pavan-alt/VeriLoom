import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import type { Question, Review } from "../../lib/types";
import { ChatComposer, ChatHistory, suggestedQuestions } from "./Reviews";

const question = (changes: Partial<Question> = {}): Question => ({
  turn_id: "turn-1",
  question: "Why was this claim flagged?",
  state: "completed",
  route: "reuse_evidence",
  answer_status: "answered",
  answer: "The approved source prohibits the phrase.",
  citations: [],
  ...changes,
});

describe("reviewer conversation", () => {
  it("shows a useful empty state before the first question", () => {
    const html = renderToStaticMarkup(<ChatHistory turns={[]} />);
    expect(html).toContain("Start a conversation about this review");
    expect(html).toContain("another grounded edit");
  });

  it("keeps each reviewer question beside its grounded AI answer", () => {
    const html = renderToStaticMarkup(<ChatHistory turns={[question()]} />);
    expect(html).toContain("Reviewer");
    expect(html).toContain("VeriLoom AI");
    expect(html).toContain("Why was this claim flagged?");
    expect(html).toContain("The approved source prohibits the phrase.");
    expect(html).toContain("Evidence route: reuse evidence");
  });

  it("distinguishes pending, clarification, and failed turns", () => {
    const html = renderToStaticMarkup(<ChatHistory turns={[
      question({ turn_id: "pending", state: "queued", answer: null, answer_status: null }),
      question({ turn_id: "clarify", answer_status: "clarification_needed", answer: "Which claim?", citations: [] }),
      question({ turn_id: "failed", state: "failed", answer: null, answer_status: null }),
    ]} />);
    expect(html).toContain("Searching approved guidance");
    expect(html).toContain("clarification needed");
    expect(html).toContain("Which claim?");
    expect(html).toContain("This answer failed");
  });

  it("offers edit refinement only with a selected finding that has a suggestion", () => {
    const finding: Review["findings"][number] = {
      finding_id: "finding-1",
      category: "tone",
      severity: "medium",
      evidence_status: "supported",
      anchor: null,
      explanation: "Use a more direct tone.",
      suggested_edit: "A clearer sentence.",
      citations: [],
    };
    const html = renderToStaticMarkup(
      <QueryClientProvider client={new QueryClient()}>
        <ChatComposer path="/review/1" revisionId="revision-1" findings={[finding]} coverage="partial" />
      </QueryClientProvider>,
    );
    expect(html).toContain("Whole review");
    expect(html).toContain("tone: Use a more direct tone.");
    expect(html).toContain("Suggested edits are proposals");
    expect(suggestedQuestions()).not.toContain("Suggest a different edit grounded in approved guidance.");
    expect(suggestedQuestions(finding)).toContain("Which source supports this finding?");
    expect(suggestedQuestions(finding)).toContain("Suggest a different edit grounded in approved guidance.");
    expect(suggestedQuestions({ ...finding, suggested_edit: null })).not.toContain("Suggest a different edit grounded in approved guidance.");
    expect(suggestedQuestions(undefined, "partial")).toContain("Why is evidence coverage partial?");
    expect(suggestedQuestions(undefined, "insufficient")).toContain("Why is evidence coverage insufficient?");
  });
});
