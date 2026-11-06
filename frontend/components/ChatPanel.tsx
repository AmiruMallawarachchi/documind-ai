"use client";

/**
 * components/ChatPanel.tsx — The transcript, the composer, and the agent status line.
 *
 * Two interface decisions carry the multi-agent design:
 *
 *  1. The status line. Verification means several seconds between the question and the first token, so the
 *     UI shows which agent is working, straight from the server's `status` frames. An unlabelled wait is
 *     indistinguishable from a hang.
 *  2. The stop control. An answer the user did not want is still spending Groq quota, so aborting is a
 *     first-class action rather than something hidden behind a reload.
 */

import { useEffect, useRef, useState } from "react";

import Message from "./Message";
import Banner from "@/components/ui/Banner";
import Button from "@/components/ui/Button";
import Spinner from "@/components/ui/Spinner";
import { FIELD, FOCUS, HINT } from "@/components/ui/tokens";
import { useChat } from "@/lib/use-chat";

type Props = {
  conversationId: string | null;
  documentIds: string[];
  indexedCount: number;
  onConversationCreated: (conversationId: string) => void;
};

export default function ChatPanel({
  conversationId,
  documentIds,
  indexedCount,
  onConversationCreated,
}: Props) {
  const [draft, setDraft] = useState("");
  const { messages, stage, busy, error, ask, stop, clearError } = useChat(
    conversationId,
    onConversationCreated,
  );

  const endRef = useRef<HTMLDivElement | null>(null);

  // Follow the answer as it streams. `behavior: "auto"` on purpose: smooth scrolling would fight the
  // frames arriving several times a second and leave the viewport lagging behind the text.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, stage]);

  const submit = () => {
    const question = draft.trim();
    if (!question || busy) return;
    ask(question, documentIds);
    setDraft("");
  };

  const disabled = indexedCount === 0 || busy;

  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-label="Conversation">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-6 sm:px-6">
        {messages.length === 0 && !busy && (
          <EmptyState indexedCount={indexedCount} />
        )}

        {messages.map((message) => (
          <Message key={message.id} message={message} />
        ))}

        {stage && (
          <p className="flex items-center gap-2 text-xs text-zinc-400" role="status" aria-live="polite">
            <Spinner className="h-3.5 w-3.5 text-indigo-400" />
            {stage}
          </p>
        )}
        <div ref={endRef} />
      </div>

      {error && (
        <Banner className="mx-4 mb-2 flex items-start justify-between gap-3 sm:mx-6">
          <p>{error}</p>
          {/* Not a `Button`: this is a link-shaped correction inside the banner, and its hover colour is
              the banner's own red. Putting it in the primitive would need a variant for one control. */}
          <button
            type="button"
            onClick={clearError}
            className={`shrink-0 underline decoration-red-800 hover:text-red-200 ${FOCUS}`}
          >
            Dismiss
          </button>
        </Banner>
      )}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        className="border-t border-zinc-800 bg-zinc-950/80 px-4 py-3 sm:px-6"
      >
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              // Enter sends, Shift+Enter makes a newline — the convention users already have from every
              // chat product, and the reason a plain button-only form is not enough.
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            rows={1}
            maxLength={8000}
            placeholder={
              indexedCount === 0
                ? "Upload a document first"
                : "Ask a question about your documents…"
            }
            disabled={indexedCount === 0}
            aria-label="Question"
            className={`${FIELD} max-h-40 min-h-[2.75rem] flex-1 resize-y`}
          />
          {busy ? (
            <Button variant="outlineDanger" className="h-11" onClick={stop}>
              Stop
            </Button>
          ) : (
            <Button type="submit" className="h-11" disabled={disabled || draft.trim().length === 0}>
              Ask
            </Button>
          )}
        </div>
        <p className={`mt-1.5 ${HINT}`}>
          Enter to send · Shift+Enter for a new line · Answers cite the passage they came from
        </p>
      </form>
    </section>
  );
}

function EmptyState({ indexedCount }: { indexedCount: number }) {
  if (indexedCount === 0) {
    return (
      <div className="mx-auto max-w-md py-10 text-center">
        <h2 className="text-sm font-semibold text-zinc-300">Nothing to answer from yet</h2>
        <p className="mt-2 text-xs leading-relaxed text-zinc-500">
          Upload a PDF, DOCX, or TXT file on the left and wait for its status to turn green. DocuMind
          only answers from documents it has finished indexing — scanned, image-only PDFs have no text to
          search.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-md py-10 text-center">
      <h2 className="text-sm font-semibold text-zinc-300">
        Ask anything about your {indexedCount} indexed{" "}
        {indexedCount === 1 ? "document" : "documents"}
      </h2>
      <p className="mt-2 text-xs text-zinc-500">
        Every factual claim in an answer is checked against the retrieved passages before it reaches
        you, and cites the page it came from.
      </p>
    </div>
  );
}
