"use client";

/**
 * components/Message.tsx — One transcript row: the answer, its citations, and the agent trace.
 *
 * The `[n]` markers are rendered as buttons rather than left as literal text because the whole promise of
 * the product is "every claim has a source": a reader who sees [2] should be able to find out what [2]
 * says in one click, not scroll hunting for a footnote.
 *
 * The trace is shown collapsed. It is the debug view that makes a multi-agent answer inspectable — which
 * passages were found, whether the critic rejected a draft — and it is exactly what a user wants when an
 * answer looks evasive, and noise when it does not.
 */

import { useState } from "react";

import type { Citation } from "@/lib/api";
import type { UiMessage } from "@/lib/use-chat";

const STAGE_LABELS: Record<string, string> = {
  router: "routed",
  retrieval: "searched",
  synthesis: "drafted",
  critique: "verified",
  finalise: "answered",
  direct: "replied",
  guard: "checked corpus",
};

function traceLabel(step: string): string {
  const [agent, detail] = step.split(":");
  const friendly = STAGE_LABELS[agent] ?? agent;
  return detail ? `${friendly} ${detail}` : friendly;
}

/** A run of adjacent markers, the only split piece that should become buttons instead of prose. */
const MARKER_RUN = /^(?:\[\d+\])+$/;

/** Split the answer on [n] markers and turn each into a button that scrolls to its citation. */
function withCitations(content: string, onJump: (index: number) => void) {
  // The repeating group has to contain the closing `]`. Split hands back exactly what it matched, so a
  // group that stops at the digits returns `[1`, which no full-marker test recognises: the citation
  // renders as broken text and its `]` disappears with the split.
  const parts = content.split(/((?:\[\d+\])+)/g);
  return parts.map((part, position) => {
    if (!MARKER_RUN.test(part)) return <span key={position}>{part}</span>;

    const indexes = [...part.matchAll(/\d+/g)].map(([digits]) => Number(digits));
    return (
      <span key={position} className="whitespace-nowrap">
        {indexes.map((index) => (
          <button
            key={index}
            type="button"
            onClick={() => onJump(index)}
            className="mx-0.5 rounded bg-indigo-500/15 px-1 align-super text-[0.7em] font-semibold text-indigo-300 transition-colors hover:bg-indigo-500/30"
            aria-label={`Go to source ${index}`}
          >
            {index}
          </button>
        ))}
      </span>
    );
  });
}

export default function Message({ message }: { message: UiMessage }) {
  const [openSource, setOpenSource] = useState<number | null>(null);
  const [showTrace, setShowTrace] = useState(false);

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
              {withCitations(message.content, jump)}
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

        {message.error && (
          <p className="rounded-lg bg-red-950/50 px-3 py-2 text-xs text-red-300">
            {message.error}
          </p>
        )}

        {message.citations.length > 0 && (
          <ul className="space-y-1.5">
            {message.citations.map((citation: Citation) => (
              <li
                key={`${citation.chunk_id}-${citation.index}`}
                id={`citation-${citation.index}`}
                className={`rounded-lg border px-3 py-2 text-xs transition-colors ${
                  openSource === citation.index
                    ? "border-indigo-500/60 bg-indigo-500/10"
                    : "border-zinc-800 bg-zinc-900/60"
                }`}
              >
                <button
                  type="button"
                  onClick={() =>
                    setOpenSource(
                      openSource === citation.index ? null : citation.index,
                    )
                  }
                  className="flex w-full items-baseline gap-2 text-left"
                  aria-expanded={openSource === citation.index}
                >
                  <span className="font-semibold text-indigo-300">
                    [{citation.index}]
                  </span>
                  <span className="truncate text-zinc-300">
                    {citation.filename}
                  </span>
                  <span className="ml-auto shrink-0 text-zinc-500">
                    p.{citation.page_number}
                  </span>
                </button>
                {openSource === citation.index && (
                  <p className="mt-2 border-t border-zinc-800 pt-2 text-zinc-400">
                    {citation.snippet}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}

        {message.agentTrace.length > 0 && !message.pending && (
          <div className="text-[0.7rem] text-zinc-500">
            <button
              type="button"
              onClick={() => setShowTrace((value) => !value)}
              className="hover:text-zinc-300"
              aria-expanded={showTrace}
            >
              {showTrace ? "Hide" : "Show"} how this was answered
            </button>
            {showTrace && (
              <ol className="mt-1.5 flex flex-wrap gap-x-2 gap-y-1">
                {message.agentTrace.map((step, position) => (
                  <li
                    key={`${step}-${position}`}
                    className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono"
                  >
                    {traceLabel(step)}
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
