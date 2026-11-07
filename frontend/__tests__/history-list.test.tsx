import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import HistoryList from "@/components/HistoryList";
import type { ConversationSummary } from "@/lib/api";

/**
 * __tests__/history-list.test.tsx — The order the sidebar claims, and the one line it reports failure in.
 *
 * Pinned-first is a frontend rule, not a server one — the backend sorts by last message and has to keep
 * doing that — so the two properties of the reorder that are easy to lose are asserted here: that the pin
 * comes before the date, and that the date order survives *inside* each group.
 */

function thread(overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    conversation_id: "conv-1",
    title: "First thread",
    updated_at: "2026-11-06T00:00:00Z",
    pinned: false,
    shared: false,
    ...overrides,
  };
}

/** Every action button in a row carries an `aria-label` that starts with the verb; the thread's own title
 *  button is the only one named by its text. `New chat` sits above the list and is named by text too. */
function titles() {
  return screen
    .getAllByRole("button")
    .filter((button) => !button.getAttribute("aria-label"))
    .filter((button) => button.textContent !== "New chat")
    .map((button) => button.textContent);
}

function renderList(conversations: ConversationSummary[], extra: Record<string, unknown> = {}) {
  return render(
    <HistoryList
      conversations={conversations}
      activeId={null}
      error={null}
      onSelect={vi.fn()}
      onRename={vi.fn().mockResolvedValue(undefined)}
      onTogglePin={vi.fn().mockResolvedValue(undefined)}
      onShare={vi.fn().mockResolvedValue("tok-abc")}
      onUnshare={vi.fn().mockResolvedValue(undefined)}
      onDelete={vi.fn().mockResolvedValue(undefined)}
      {...extra}
    />,
  );
}

describe("HistoryList", () => {
  it("puts pinned threads first and keeps last-answered order within each group", () => {
    // What the server actually sends: newest answer first. The two pinned rows are in that order too, so
    // the only thing moving them is the pin.
    renderList([
      thread({ conversation_id: "a", title: "Pinned newer", pinned: true, updated_at: "2026-11-06T04:00:00Z" }),
      thread({ conversation_id: "c", title: "Newest", updated_at: "2026-11-06T03:00:00Z" }),
      thread({ conversation_id: "d", title: "Second", updated_at: "2026-11-06T02:00:00Z" }),
      thread({ conversation_id: "b", title: "Pinned old", pinned: true, updated_at: "2026-11-06T01:00:00Z" }),
    ]);

    expect(titles()).toEqual(["Pinned newer", "Pinned old", "Newest", "Second"]);
  });

  it("opens a new chat when told to, passing no thread at all", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    renderList([thread()], { onSelect });

    await user.click(screen.getByRole("button", { name: "New chat" }));

    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it("counts the threads it was given", () => {
    renderList([thread(), thread({ conversation_id: "2", title: "Second thread" })]);

    expect(screen.getByText("2 saved")).toBeInTheDocument();
  });

  it("says so when a write failed, in the list's own voice", () => {
    renderList([thread()], { error: "Try again in 41 seconds." });

    expect(screen.getByRole("status")).toHaveTextContent("Try again in 41 seconds.");
  });

  it("says nothing about failures it did not get", () => {
    renderList([thread()]);

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
