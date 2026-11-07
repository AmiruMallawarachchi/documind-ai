/**
 * components/SharedTranscript.tsx — A thread as its recipient sees it.
 *
 * The same two bubbles the workspace draws, and nothing else: no source list, no agent trace, no document
 * names, and no question box at the bottom. The backend's shared view has no citations to render, so this
 * component could not invent them — and the one thing it must not do is turn the `[2]` still sitting in the
 * answer's prose into a button, because a chip that jumps nowhere is worse than a number.
 *
 * So `parseAnswer` splits the markers out here too, and they come back as inert superscripts. The sentence
 * above the transcript says why, in the recipient's terms rather than the product's.
 */

import Link from "next/link";

import { FOCUS } from "@/components/ui/tokens";
import { parseAnswer } from "@/lib/citations";
import { formatDate } from "@/lib/format";
import type { SharedMessage } from "@/lib/api";

type Props = {
  title: string;
  updatedAt: string;
  messages: SharedMessage[];
};

export default function SharedTranscript({ title, updatedAt, messages }: Props) {
  return (
    <div className="space-y-5">
      <header className="space-y-2 border-b border-zinc-800 pb-4">
        <h1 className="text-lg font-semibold text-zinc-100">{title}</h1>
        <p className="text-xs text-zinc-500">Shared from DocuMind · {formatDate(updatedAt)}</p>
        <p className="text-xs leading-relaxed text-zinc-500">
          Answers only. The documents behind them, and the passages quoted from them, stay with the person
          who uploaded them — so a number like <span className="font-semibold text-indigo-300">[2]</span>{" "}
          in an answer marks a claim that had a source, and asking the author is how you get it.
        </p>
      </header>

      <div className="space-y-4">
        {messages.map((message, position) =>
          message.role === "user" ? (
            <div key={position} className="flex justify-end">
              <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2.5 text-sm text-white">
                {message.content}
              </p>
            </div>
          ) : (
            <div key={position} className="flex justify-start">
              <div className="rounded-2xl rounded-bl-sm border border-zinc-800 bg-zinc-900 px-4 py-3 text-sm leading-relaxed text-zinc-100">
                <p className="whitespace-pre-wrap">
                  {parseAnswer(message.content).map((part, index) =>
                    part.kind === "text" ? (
                      <span key={index}>{part.text}</span>
                    ) : (
                      <span key={index} className="whitespace-nowrap">
                        {part.indexes.map((marker) => (
                          <sup
                            key={marker}
                            className="mx-0.5 text-[0.7em] font-semibold text-indigo-300"
                          >
                            {marker}
                          </sup>
                        ))}
                      </span>
                    ),
                  )}
                </p>
              </div>
            </div>
          ),
        )}
      </div>

      <footer className="border-t border-zinc-800 pt-4 text-xs text-zinc-500">
        <Link
          href="/"
          className={`rounded font-medium text-indigo-400 transition-colors hover:text-indigo-300 ${FOCUS}`}
        >
          Ask about your own documents on DocuMind
        </Link>
      </footer>
    </div>
  );
}
