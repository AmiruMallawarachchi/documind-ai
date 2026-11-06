"use client";

/**
 * components/HistoryList.tsx — The threads this account already had, newest first.
 *
 * Opening one is a whole remount of the chat panel rather than a swap of the transcript in place, so this
 * list only ever says *which* thread the reader wants; `app/page.tsx` decides what that means for the rest
 * of the tree. The server names a thread after its first question, which is why a brand-new conversation
 * appears here without this component knowing anything about it.
 */

import Button from "@/components/ui/Button";
import EmptyNote from "@/components/ui/EmptyNote";
import { FOCUS } from "@/components/ui/tokens";
import type { ConversationSummary } from "@/lib/api";

type Props = {
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (conversationId: string | null) => void;
};

export default function HistoryList({ conversations, activeId, onSelect }: Props) {
  return (
    <>
      <div className="flex items-center gap-2">
        <Button size="xs" onClick={() => onSelect(null)}>
          New chat
        </Button>
        <span className="text-[0.7rem] text-zinc-600">{conversations.length} saved</span>
      </div>
      <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto">
        {conversations.map((conversation) => (
          <li key={conversation.conversation_id}>
            {/* Not a `<Button>`: a thread is a full-width, left-aligned, truncating line, and its active
                state is a background, not a variant. */}
            <button
              type="button"
              onClick={() => onSelect(conversation.conversation_id)}
              className={`w-full truncate rounded-lg px-2 py-1.5 text-left text-xs transition-colors ${FOCUS} ${
                conversation.conversation_id === activeId
                  ? "bg-zinc-800 text-zinc-100"
                  : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
              }`}
              title={conversation.title}
            >
              {conversation.title}
            </button>
          </li>
        ))}
        {conversations.length === 0 && <EmptyNote>No conversations yet.</EmptyNote>}
      </ul>
    </>
  );
}
