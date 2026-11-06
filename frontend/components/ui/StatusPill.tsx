"use client";

/**
 * ui/StatusPill.tsx — What the indexing pipeline thinks of a document, in one word and one colour.
 *
 * The word is the server's status verbatim except where it would confuse: `uploading` is the queue
 * position, not a transfer, so it reads "queued", and both transient states share the amber so a row
 * that is still working looks like it is still working.
 *
 * Colour is never the only signal — the label is always present, because emerald and red are the two
 * colours this interface cannot assume a reader distinguishes.
 */

import type { DocumentStatus } from "@/lib/api";

const STATUS = {
  uploading: { label: "queued", tone: "bg-amber-500/15 text-amber-300" },
  parsing: { label: "indexing", tone: "bg-amber-500/15 text-amber-300" },
  indexed: { label: "ready", tone: "bg-emerald-500/15 text-emerald-300" },
  failed: { label: "failed", tone: "bg-red-500/15 text-red-300" },
} as const;

export default function StatusPill({ status }: { status: DocumentStatus }) {
  const { label, tone } = STATUS[status];
  return (
    <span className={`rounded px-1.5 py-0.5 font-medium ${tone}`}>{label}</span>
  );
}
