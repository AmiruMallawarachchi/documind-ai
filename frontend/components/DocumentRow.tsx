"use client";

/**
 * components/DocumentRow.tsx — One file in the corpus: its state, its size, its delete confirmation.
 *
 * The confirmation lives here rather than in the panel because it is a fact about this row and nobody
 * else. As a panel-level `confirming: string | null` it also meant the panel re-rendered every row on
 * every click, and that opening a second confirmation silently closed the first — behaviour nobody chose,
 * just what a single id made easy.
 *
 * Deleting is the one irreversible thing a user can do here, which is why the row asks instead of
 * answering an icon click: the vectors and the Mongo row both go.
 */

import { useState } from "react";

import Button from "@/components/ui/Button";
import { CloseIcon } from "@/components/ui/icons";
import StatusPill from "@/components/ui/StatusPill";
import { FOCUS } from "@/components/ui/tokens";
import { formatBytes } from "@/lib/format";
import type { DocumentRecord } from "@/lib/api";

type Props = {
  document: DocumentRecord;
  selected: boolean;
  onToggleScope: (documentId: string) => void;
  onRemove: (documentId: string) => void;
};

export default function DocumentRow({
  document,
  selected,
  onToggleScope,
  onRemove,
}: Props) {
  const [confirming, setConfirming] = useState(false);
  const isReady = document.status === "indexed";

  return (
    <li className="rounded-lg px-2 py-2.5 transition-colors hover:bg-zinc-900/60">
      <div className="flex items-start gap-2">
        <label className="mt-0.5 shrink-0">
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggleScope(document.document_id)}
            disabled={!isReady}
            aria-label={`Search ${document.filename}`}
            className={`h-3.5 w-3.5 accent-indigo-500 disabled:opacity-30 ${FOCUS}`}
          />
        </label>

        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium text-zinc-200" title={document.filename}>
            {document.filename}
          </p>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[0.7rem] text-zinc-500">
            <StatusPill status={document.status} />
            {isReady && (
              <span>
                {document.chunk_count} {document.chunk_count === 1 ? "chunk" : "chunks"}
                {document.page_count > 0 &&
                  ` · ${document.page_count} ${document.page_count === 1 ? "page" : "pages"}`}
              </span>
            )}
            <span>{formatBytes(document.size_bytes)}</span>
          </p>

          {document.error && (
            <p className="mt-1.5 text-[0.7rem] leading-snug text-red-400">{document.error}</p>
          )}
        </div>

        {confirming ? (
          <div className="flex shrink-0 gap-1">
            <Button
              variant="danger"
              size="xs"
              onClick={() => {
                setConfirming(false);
                onRemove(document.document_id);
              }}
            >
              Delete
            </Button>
            <Button variant="outline" size="xs" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        ) : (
          <Button
            variant="icon"
            size="xs"
            onClick={() => setConfirming(true)}
            aria-label={`Delete ${document.filename}`}
            className="shrink-0"
          >
            <CloseIcon />
          </Button>
        )}
      </div>
    </li>
  );
}
