"use client";

/**
 * components/Message.tsx — One transcript row, assembled from the three things a row is made of.
 *
 * The answer bubble, its sources (`SourceList`) and the debug trail behind it (`AgentTrace`). This file
 * keeps the bubble itself, because the bubble is the message, and the one piece of state two of the three
 * children need — which source is open, because a `[2]` in the prose and the `[2]` row below are the same
 * disclosure. Everything a single child owns stays inside that child.
 *
 * The `[n]` markers are rendered as buttons rather than left as literal text because the whole promise of
 * the product is "every claim has a source": a reader who sees [2] should be able to find out what [2]
 * says in one click, not scroll hunting for a footnote. Splitting them out of the sentence is `lib/citations`.
 */

import { useState } from "react";

import AgentTrace from "@/components/AgentTrace";
import SourceList from "@/components/SourceList";
import Banner from "@/components/ui/Banner";
import { parseAnswer } from "@/lib/citations";
import type { UiMessage } from "@/lib/use-chat";

export default function Message({ message }: { message: UiMessage }) {
  const [openSource, setOpenSource] = useState<number | null>(null);

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2.5 text-sm text-white">
          {message.content}
        </p>
      </div>
    );
  }

  const jump = (index: number) => {
    setOpenSource(index);
    document
      .getElementById(`citation-${index}`)
      ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  return (
    <div className="flex justify-start">
      <div className="max-w-[92%] space-y-3">
        <div className="rounded-2xl rounded-bl-sm border border-zinc-800 bg-zinc-900 px-4 py-3 text-sm leading-relaxed text-zinc-100">
          {message.content ? (
            <p className="whitespace-pre-wrap">
              {parseAnswer(message.content).map((part, position) =>
                part.kind === "text" ? (
                  <span key={position}>{part.text}</span>
                ) : (
                  <span key={position} className="whitespace-nowrap">
                    {part.indexes.map((index) => (
                      <button
                        key={index}
                        type="button"
                        onClick={() => jump(index)}
                        className="mx-0.5 rounded bg-indigo-500/15 px-1 align-super text-[0.7em] font-semibold text-indigo-300 transition-colors hover:bg-indigo-500/30"
                        aria-label={`Go to source ${index}`}
                      >
                        {index}
                      </button>
                    ))}
                  </span>
                ),
              )}
            </p>
          ) : message.pending ? (
            <p className="text-zinc-500">Working on it…</p>
          ) : (
            // Stopped before the first token arrived. Without this line the row is an empty box, which is
            // indistinguishable from a bug; the question is still on screen, so say what happened to it.
            <p className="text-zinc-500">
              You stopped this answer before it produced any text.
            </p>
          )}

          {message.pending && (
            <span
              className="ml-1 inline-block h-4 w-1.5 animate-pulse bg-indigo-400 align-middle"
              aria-hidden
            />
          )}
        </div>

        {message.error && <Banner>{message.error}</Banner>}

        {message.citations.length > 0 && (
          <SourceList
            citations={message.citations}
            openIndex={openSource}
            onToggle={(index) =>
              setOpenSource(openSource === index ? null : index)
            }
          />
        )}

        {message.agentTrace.length > 0 && !message.pending && (
          <AgentTrace steps={message.agentTrace} />
        )}
      </div>
    </div>
  );
}
