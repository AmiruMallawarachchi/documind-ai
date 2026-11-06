import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { Citation } from "@/lib/api";
import type { UiMessage } from "@/lib/use-chat";
import Message from "@/components/Message";

/**
 * __tests__/message.test.tsx — How an answer's `[n]` markers become clickable sources.
 *
 * These cases exist because the marker parser silently matched nothing at all: the split regex captured
 * `[1` and the parser only recognised `[1]`, so every citation in the product rendered as a broken
 * bracket with its closing one deleted, and not a single button appeared. No build, type-check, or
 * end-to-end flow noticed — the text was still readable. That is the class of bug this file is for.
 */

function citation(index: number, filename = "loan.pdf"): Citation {
  return {
    index,
    document_id: `doc-${index}`,
    filename,
    chunk_id: `doc-${index}_0000`,
    page_number: index,
    snippet: `Passage ${index}.`,
  };
}

function answer(content: string, citations: Citation[] = [citation(1)]): UiMessage {
  return {
    id: "m1",
    role: "assistant",
    content,
    citations,
    agentTrace: ["router:search", "retrieval:2", "critique:pass"],
    pending: false,
    error: null,
  };
}

function renderAnswer(content: string, citations: Citation[] = [citation(1)]) {
  return render(<Message message={answer(content, citations)} />);
}

/**
 * The prose of the answer bubble, buttons included — the digit inside a `<button>` counts as its text.
 *
 * Assertions about punctuation have to be made against the whole paragraph, because `getByText` hands
 * back the innermost `<span>` that matches and a span never sees the sentence either side of it.
 */
function bubble(container: HTMLElement): string {
  return container.querySelector("p")?.textContent ?? "";
}

describe("citation markers", () => {
  it("turns a marker into a button and keeps the sentence punctuation", () => {
    const { container } = renderAnswer("The fee is 250 dollars [1].");

    expect(screen.getByRole("button", { name: "Go to source 1" })).toBeInTheDocument();
    // The bug this guards: the split left `[1` in the prose and deleted its `]`.
    expect(bubble(container)).toBe("The fee is 250 dollars 1.");
  });

  it("renders one button per marker when they are chained", () => {
    const { container } = renderAnswer("Both clauses apply [1][3].", [citation(1), citation(3)]);

    expect(screen.getByRole("button", { name: "Go to source 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go to source 3" })).toBeInTheDocument();
    expect(bubble(container)).toBe("Both clauses apply 13.");
  });

  it("handles a two-digit passage number", () => {
    const { container } = renderAnswer("See the schedule [10].", [citation(10)]);

    expect(screen.getByRole("button", { name: "Go to source 10" })).toBeInTheDocument();
    expect(bubble(container)).toBe("See the schedule 10.");
  });

  it("leaves brackets that are not markers alone in the text", () => {
    const { container } = renderAnswer('The clause "[signed copy only]" is quoted.');

    expect(screen.queryByRole("button", { name: /Go to source/ })).not.toBeInTheDocument();
    expect(bubble(container)).toBe('The clause "[signed copy only]" is quoted.');
  });

  it("splits a marker sitting between two sentences", () => {
    const { container } = renderAnswer("First claim [1]. Second claim [2].", [citation(1), citation(2)]);

    expect(bubble(container)).toBe("First claim 1. Second claim 2.");
  });
});

describe("citation jump", () => {
  it("scrolls to the matching source when a marker is clicked", async () => {
    const user = userEvent.setup();
    render(<Message message={answer("The fee is 250 dollars [2].", [citation(2, "lease.pdf")])} />);

    await user.click(screen.getByRole("button", { name: "Go to source 2" }));

    expect(document.getElementById("citation-2")).toBeInTheDocument();
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });
});

describe("the empty and failed states", () => {
  it("says it is working while the draft is still arriving", () => {
    render(<Message message={{ ...answer(""), pending: true }} />);

    expect(screen.getByText(/Working on it/)).toBeInTheDocument();
  });

  it("explains a turn stopped before the first token", () => {
    render(<Message message={answer("")} />);

    expect(screen.queryByText(/Working on it/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Show how this was answered/i })).toBeInTheDocument();
  });
});
