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
          <p
            className="flex items-center gap-2 text-xs text-zinc-400"
            role="status"
            aria-live="polite"
          >
            <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-indigo-400 border-t-transparent" />
            {stage}
          </p>
        )}
        <div ref={endRef} />
      </div>

      {error && (
        <div className="mx-4 mb-2 flex items-start justify-between gap-3 rounded-lg bg-red-950/50 px-3 py-2 text-xs text-red-300 sm:mx-6">
          <p>{error}</p>
          <button
            type="button"
            onClick={clearError}
            className="shrink-0 underline decoration-red-800 hover:text-red-200"
          >
            Dismiss
          </button>
        </div>
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
            className="max-h-40 min-h-[2.75rem] flex-1 resize-y rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
          />
          {busy ? (
            <button
              type="button"
              onClick={stop}
              className="h-11 rounded-xl border border-zinc-700 px-4 text-sm font-medium text-zinc-200 transition-colors hover:border-red-800 hover:text-red-300"
            >
              Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={disabled || draft.trim().length === 0}
              className="h-11 rounded-xl bg-indigo-600 px-4 text-sm font-semibold text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-600"
            >
              Ask
            </button>
          )}
        </div>
        <p className="mt-1.5 text-[0.7rem] text-zinc-600">
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
