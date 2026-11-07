import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ConversationRow from "@/components/ConversationRow";
import { FOCUS } from "@/components/ui/tokens";
import type { ConversationSummary } from "@/lib/api";

/**
 * __tests__/conversation-row.test.tsx — What a thread's three controls promise, and when they keep it.
 *
 * The rules worth holding here are the ones a hand-wired row gets wrong: that a click on the title opens
 * the thread rather than renaming it, that Save sends text and Cancel sends nothing, and that a row's
 * editor is its own — the same reason `document-row.test.tsx` lets two rows ask about deletion at once.
 */

function thread(overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    conversation_id: "conv-1",
    title: "Vendor agreement terms",
    updated_at: "2026-11-06T00:00:00Z",
    pinned: false,
    shared: false,
    ...overrides,
  };
}

function renderRow(conversation = thread(), extra: Partial<Parameters<typeof ConversationRow>[0]> = {}) {
  const props = {
    active: false,
    onSelect: vi.fn(),
    onRename: vi.fn().mockResolvedValue(undefined),
    onTogglePin: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn().mockResolvedValue(undefined),
    ...extra,
  };
  return {
    props,
    ...render(
      <ul>
        <ConversationRow conversation={conversation} {...props} />
      </ul>,
    ),
  };
}

describe("ConversationRow", () => {
  it("opens the thread when the title is clicked, and nothing else", async () => {
    const user = userEvent.setup();
    const { props } = renderRow();

    await user.click(screen.getByRole("button", { name: "Vendor agreement terms" }));

    expect(props.onSelect).toHaveBeenCalledWith("conv-1");
    expect(props.onRename).not.toHaveBeenCalled();
    expect(props.onDelete).not.toHaveBeenCalled();
  });

  it("gives the raw title button the house keyboard ring", () => {
    renderRow();

    expect(screen.getByRole("button", { name: "Vendor agreement terms" }).className).toContain(FOCUS);
  });

  it("marks a pinned thread and offers to unpin it", () => {
    renderRow(thread({ pinned: true }));

    expect(screen.getByRole("button", { name: "Unpin Vendor agreement terms" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Pin / })).not.toBeInTheDocument();
  });

  it("pins with the flag it wants, not the flag it had", async () => {
    const user = userEvent.setup();
    const { props } = renderRow();

    await user.click(screen.getByRole("button", { name: "Pin Vendor agreement terms" }));

    expect(props.onTogglePin).toHaveBeenCalledWith("conv-1", true);
  });

  it("renames through a field that starts with the current title", async () => {
    const user = userEvent.setup();
    const { props } = renderRow();

    await user.click(screen.getByRole("button", { name: "Rename Vendor agreement terms" }));
    const field = screen.getByRole("textbox", { name: "New title for Vendor agreement terms" });
    expect(field).toHaveValue("Vendor agreement terms");

    await user.clear(field);
    await user.type(field, "Late fees, clause 4");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(props.onRename).toHaveBeenCalledWith("conv-1", "Late fees, clause 4");
  });

  it("sends nothing when the title did not change", async () => {
    const user = userEvent.setup();
    const { props } = renderRow();

    await user.click(screen.getByRole("button", { name: "Rename Vendor agreement terms" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(props.onRename).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Vendor agreement terms" })).toBeInTheDocument();
  });

  it("sends nothing for a title that is only spaces", async () => {
    const user = userEvent.setup();
    const { props } = renderRow();

    await user.click(screen.getByRole("button", { name: "Rename Vendor agreement terms" }));
    const field = screen.getByRole("textbox", { name: "New title for Vendor agreement terms" });
    await user.clear(field);
    await user.type(field, "   ");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(props.onRename).not.toHaveBeenCalled();
  });

  it("puts the editor away on Escape without sending it", async () => {
    const user = userEvent.setup();
    const { props } = renderRow();

    await user.click(screen.getByRole("button", { name: "Rename Vendor agreement terms" }));
    const field = screen.getByRole("textbox", { name: "New title for Vendor agreement terms" });
    await user.type(field, "abandoned");
    await user.keyboard("{Escape}");

    expect(props.onRename).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Vendor agreement terms" })).toBeInTheDocument();
  });

  it("deletes only after being asked twice", async () => {
    const user = userEvent.setup();
    const { props } = renderRow();

    await user.click(screen.getByRole("button", { name: "Delete Vendor agreement terms" }));
    expect(props.onDelete).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(props.onDelete).toHaveBeenCalledWith("conv-1");
  });

  it("keeps the thread when the confirmation is refused", async () => {
    const user = userEvent.setup();
    const { props } = renderRow();

    await user.click(screen.getByRole("button", { name: "Delete Vendor agreement terms" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(props.onDelete).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Vendor agreement terms" })).toBeInTheDocument();
  });

  it("holds its own state: two rows, each doing something different", async () => {
    const user = userEvent.setup();
    render(
      <ul>
        <ConversationRow
          conversation={thread({ conversation_id: "a", title: "First" })}
          active={false}
          onSelect={vi.fn()}
          onRename={vi.fn().mockResolvedValue(undefined)}
          onTogglePin={vi.fn().mockResolvedValue(undefined)}
          onDelete={vi.fn().mockResolvedValue(undefined)}
        />
        <ConversationRow
          conversation={thread({ conversation_id: "b", title: "Second" })}
          active={false}
          onSelect={vi.fn()}
          onRename={vi.fn().mockResolvedValue(undefined)}
          onTogglePin={vi.fn().mockResolvedValue(undefined)}
          onDelete={vi.fn().mockResolvedValue(undefined)}
        />
      </ul>,
    );

    await user.click(screen.getByRole("button", { name: "Rename First" }));
    await user.click(screen.getByRole("button", { name: "Delete Second" }));

    expect(screen.getByRole("textbox", { name: "New title for First" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
    // The row that was only asked about deletion still shows its own title, and vice versa.
    expect(screen.getByRole("button", { name: "Second" })).toBeInTheDocument();
  });

  it("leaves the editor open with its draft when the server refuses", async () => {
    const user = userEvent.setup();
    const onRename = vi.fn().mockRejectedValue(new Error("offline"));
    renderRow(thread(), { onRename });

    await user.click(screen.getByRole("button", { name: "Rename Vendor agreement terms" }));
    const field = screen.getByRole("textbox", { name: "New title for Vendor agreement terms" });
    await user.clear(field);
    await user.type(field, "Kept because it failed");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(field).toHaveValue("Kept because it failed");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });
});
