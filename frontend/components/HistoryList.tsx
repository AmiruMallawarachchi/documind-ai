"use client";

/**
 * components/HistoryList.tsx — The threads this account already had, pinned ones first.
 *
 * Opening one is a whole remount of the chat panel rather than a swap of the transcript in place, so this
 * list only ever says *which* thread the reader wants; `app/page.tsx` decides what that means for the rest
 * of the tree, and owns the rename/pin/delete writes for the same reason — the list is its state, and
 * deleting the thread that is on screen has to reach both.
 *
 * The server names a thread after its first question, which is why a brand-new conversation appears here
 * without this component knowing anything about it.
 *
 * Pinned rows come first, then most-recently-answered. Sorting is done here rather than requested of the
 * backend because `updated_at` means *last message* and has to keep meaning it: a `PATCH` that also bumped
 * the timestamp to force the order would fling a six-month-old thread to the top of the list for someone
 * who only wanted to call it something else.
 */

import { useMemo } from "react";

import Button from "@/components/ui/Button";
import ConversationRow from "@/components/ConversationRow";
import EmptyNote from "@/components/ui/EmptyNote";
import type { ConversationSummary } from "@/lib/api";

type Props = {
  conversations: ConversationSummary[];
  activeId: string | null;
  error: string | null;
  /** `null` is "no thread yet", which is what New chat means. */
  onSelect: (conversationId: string | null) => void;
  onRename: (conversationId: string, title: string) => Promise<void>;
  onTogglePin: (conversationId: string, pinned: boolean) => Promise<void>;
  onDelete: (conversationId: string) => Promise<void>;
};

export default function HistoryList({
  conversations,
  activeId,
  error,
  onSelect,
  onRename,
  onTogglePin,
  onDelete,
}: Props) {
  // `Array.prototype.sort` is stable in every engine this app ships to, so the server's
  // newest-answered-first order survives inside each of the two groups.
  const ordered = useMemo(
    () =>
      [...conversations].sort(
        (a, b) => Number(b.pinned) - Number(a.pinned),
      ),
    [conversations],
  );

  return (
    <>
      <div className="flex items-center gap-2">
        <Button size="xs" onClick={() => onSelect(null)}>
          New chat
        </Button>
        <span className="text-[0.7rem] text-zinc-600">{conversations.length} saved</span>
      </div>
      {error && (
        <p className="mt-2 text-[0.7rem] leading-relaxed text-red-400" role="status">
          {error}
        </p>
      )}
      <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto">
        {ordered.map((conversation) => (
          <ConversationRow
            key={conversation.conversation_id}
            conversation={conversation}
            active={conversation.conversation_id === activeId}
            onSelect={onSelect}
            onRename={onRename}
            onTogglePin={onTogglePin}
            onDelete={onDelete}
          />
        ))}
        {conversations.length === 0 && <EmptyNote>No conversations yet.</EmptyNote>}
      </ul>
    </>
  );
}
