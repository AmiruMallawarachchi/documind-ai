import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SharedTranscript from "@/components/SharedTranscript";
import type { SharedMessage } from "@/lib/api";

/**
 * __tests__/shared-transcript.test.tsx — What a stranger is shown, and what is not in the DOM at all.
 *
 * The promise this file holds is the one the whole share feature is built on: the answers travel, the
 * sources do not. That is enforced at the backend's projection and again by `SharedMessage` having no
 * citation field — but this component is where a reader would notice the difference, so the assertions here
 * are about what the page *does* with a `[2]` left sitting in the prose, and about the workspace controls
 * that must not appear on a page nobody can type into.
 */

function message(overrides: Partial<SharedMessage> = {}): SharedMessage {
  return {
    role: "assistant",
    content: "The agreement renews on 1 March 2027 [1] and caps the fee at 4 % [2][3].",
    created_at: "2026-11-06T12:00:00Z",
    ...overrides,
  };
}

function renderThread(messages: SharedMessage[] = [message()], extra: Partial<{ title: string; updatedAt: string }> = {}) {
  return render(
    <SharedTranscript
      title="Vendor agreement terms"
      updatedAt="2026-11-06T12:00:00Z"
      messages={messages}
      {...extra}
    />,
  );
}

describe("SharedTranscript", () => {
  it("shows the title and both sides of the conversation", () => {
    renderThread([message({ role: "user", content: "When does it renew?" }), message()]);

    expect(screen.getByRole("heading", { name: "Vendor agreement terms" })).toBeInTheDocument();
    expect(screen.getByText("When does it renew?")).toBeInTheDocument();
    expect(screen.getByText(/renews on 1 March 2027/)).toBeInTheDocument();
  });

  it("leaves a citation marker as a number rather than a dead button", () => {
    const { container } = renderThread();

    // In the workspace `[2]` is a chip that scrolls to a passage. Here there is no passage to scroll to, so
    // a button would be an affordance that goes nowhere — the worst kind of broken link.
    expect(screen.queryByRole("button", { name: /source/i })).not.toBeInTheDocument();
    expect(container.querySelectorAll("sup").length).toBeGreaterThan(0);
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("keeps a bracket that is not a marker inside the sentence", () => {
    renderThread([message({ content: "Signed copy only [signed copy only] for the audit." })]);

    expect(screen.getByText(/Signed copy only \[signed copy only\]/)).toBeInTheDocument();
  });

  it("says, in the reader's terms, why the numbers have nothing behind them", () => {
    renderThread();

    expect(screen.getByText(/Answers only/)).toBeInTheDocument();
  });

  it("carries none of the workspace: no question box, no documents, no upload", () => {
    renderThread();

    expect(screen.queryByRole("textbox", { name: "Question" })).not.toBeInTheDocument();
    expect(screen.queryByText("Upload documents")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ask" })).not.toBeInTheDocument();
  });

  it("dates the thread and offers the way home", () => {
    renderThread();

    expect(screen.getByText(/6 November 2026/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /DocuMind/ })).toHaveAttribute("href", "/");
  });

  it("renders a thread whose answer has no markers without inventing any", () => {
    const { container } = renderThread([message({ content: "Nothing was retrieved for that question." })]);

    expect(container.querySelectorAll("sup")).toHaveLength(0);
    expect(screen.getByText("Nothing was retrieved for that question.")).toBeInTheDocument();
  });
});
