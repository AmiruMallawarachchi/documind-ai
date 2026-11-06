"use client";

/**
 * components/SourceList.tsx — What the answer's `[n]` markers point at.
 *
 * The list is collapsed to a filename and a page until you open it, because a four-citation answer that
 * shows four snippets turns the transcript into a wall of quoted text and hides the sentence the reader
 * came for. Opening is a click on the row, and the row keeps its `[n]` so the marker in the answer and the
 * entry here are visibly the same thing.
 *
 * Which row is open is the parent's to know: the marker buttons in the answer set it as well as the rows
 * here, so the state cannot live in one of the two children.
 */

import { FOCUS } from "@/components/ui/tokens";
import type { Citation } from "@/lib/api";

type Props = {
  citations: Citation[];
  openIndex: number | null;
  onToggle: (index: number) => void;
};

export default function SourceList({ citations, openIndex, onToggle }: Props) {
  return (
    <ul className="space-y-1.5">
      {citations.map((citation: Citation) => (
        <li
          key={`${citation.chunk_id}-${citation.index}`}
          id={`citation-${citation.index}`}
          className={`rounded-lg border px-3 py-2 text-xs transition-colors ${
            openIndex === citation.index
              ? "border-indigo-500/60 bg-indigo-500/10"
              : "border-zinc-800 bg-zinc-900/60"
          }`}
        >
          <button
            type="button"
            onClick={() => onToggle(citation.index)}
            className={`flex w-full items-baseline gap-2 text-left ${FOCUS}`}
            aria-expanded={openIndex === citation.index}
          >
            <span className="font-semibold text-indigo-300">[{citation.index}]</span>
            <span className="truncate text-zinc-300">{citation.filename}</span>
            <span className="ml-auto shrink-0 text-zinc-500">p.{citation.page_number}</span>
          </button>
          {openIndex === citation.index && (
            <p className="mt-2 border-t border-zinc-800 pt-2 text-zinc-400">{citation.snippet}</p>
          )}
        </li>
      ))}
    </ul>
  );
}
