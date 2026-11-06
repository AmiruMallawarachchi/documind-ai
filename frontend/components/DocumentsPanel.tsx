"use client";

/**
 * components/DocumentsPanel.tsx — Upload, watch indexing, choose which files a question searches.
 *
 * Indexing is asynchronous, so this panel is a poller's view of the pipeline: a row shows `uploading`
 * until the background task finishes and then flips to `indexed` or `failed` with the backend's own error
 * text. The failed case is shown inline rather than as a toast because the reason ("this PDF is scanned,
 * no text layer") is something the user acts on, not something they dismiss.
 *
 * Scope selection is per-question and defaults to "everything indexed". Restricting it is the difference
 * between "what does the loan agreement say" pulling passages from three unrelated files and answering
 * from one.
 *
 * The drop target is the whole panel, not a dashed rectangle drawn around a button: the ring only appears
 * while a file is actually over it, so the affordance shows up when it is true instead of sitting there
 * permanently claiming the eye.
 */

import { useRef, useState } from "react";

import type { DocumentRecord, DocumentStatus } from "@/lib/api";
import type { DocumentsState } from "@/lib/use-documents";

const STATUS_STYLE: Record<DocumentStatus, string> = {
  uploading: "bg-amber-500/15 text-amber-300",
  parsing: "bg-amber-500/15 text-amber-300",
  indexed: "bg-emerald-500/15 text-emerald-300",
  failed: "bg-red-500/15 text-red-300",
};

const STATUS_TEXT: Record<DocumentStatus, string> = {
  uploading: "queued",
  parsing: "indexing",
  indexed: "ready",
  failed: "failed",
};

const ACCEPTED = ".pdf,.docx,.txt";

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

type Props = {
  state: DocumentsState;
  selectedIds: string[];
  onToggleScope: (documentId: string) => void;
  onScopeChange: (documentIds: string[]) => void;
};

export default function DocumentsPanel({
  state,
  selectedIds,
  onToggleScope,
  onScopeChange,
}: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);

  const indexed = state.documents.filter((row) => row.status === "indexed");
  const pending = state.documents.filter(
    (row) => row.status === "uploading" || row.status === "parsing",
  );

  const handleFiles = (list: FileList | null) => {
    if (!list || list.length === 0) return;
    void state.upload(Array.from(list));
  };

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        handleFiles(event.dataTransfer.files);
      }}
      className={`flex min-h-0 flex-col gap-3 rounded-xl transition-shadow ${
        dragging ? "ring-1 ring-indigo-500/70" : ""
      }`}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPTED}
        onChange={(event) => {
          handleFiles(event.target.files);
          // Reset so re-selecting the same file fires change again.
          event.target.value = "";
        }}
        className="hidden"
        aria-label="Choose files to upload"
      />
      <div className="space-y-1.5">
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={state.uploading}
          aria-busy={state.uploading}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed"
        >
          {state.uploading && (
            <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden>
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z" />
            </svg>
          )}
          Upload documents
        </button>
        <p className="text-[0.7rem] leading-snug text-zinc-600">
          PDF, DOCX, TXT · up to 10 MB each · drop files anywhere in this panel
        </p>
      </div>

      {state.error && (
        <p className="rounded-lg bg-red-950/50 px-3 py-2 text-xs text-red-300">{state.error}</p>
      )}

      <div className="flex items-center justify-between text-[0.7rem] text-zinc-500">
        <span>
          {indexed.length} ready
          {pending.length > 0 && ` · ${pending.length} indexing`}
        </span>
        {indexed.length > 0 && (
          <button
            type="button"
            onClick={() =>
              onScopeChange(
                selectedIds.length === indexed.length
                  ? []
                  : indexed.map((row) => row.document_id),
              )
            }
            className="hover:text-zinc-300"
          >
            {selectedIds.length === 0
              ? "select all"
              : selectedIds.length === indexed.length
                ? "clear (search all)"
                : "select all"}
          </button>
        )}
      </div>

      <ul className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
        {state.loading && (
          <li className="px-2 py-1.5 text-xs text-zinc-500">Loading your documents…</li>
        )}

        {!state.loading && state.documents.length === 0 && (
          <li className="px-2 py-1.5 text-xs leading-relaxed text-zinc-500">
            Nothing uploaded yet. This account has its own document space, so the list is yours alone.
          </li>
        )}

        {state.documents.map((document: DocumentRecord) => {
          const isReady = document.status === "indexed";
          return (
            <li
              key={document.document_id}
              className="-mx-2 rounded-lg px-2 py-2.5 transition-colors hover:bg-zinc-900/60"
            >
              <div className="flex items-start gap-2">
                <label className="mt-0.5 shrink-0">
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(document.document_id)}
                    onChange={() => onToggleScope(document.document_id)}
                    disabled={!isReady}
                    aria-label={`Search ${document.filename}`}
                    className="h-3.5 w-3.5 accent-indigo-500 disabled:opacity-30"
                  />
                </label>

                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-medium text-zinc-200" title={document.filename}>
                    {document.filename}
                  </p>
                  <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[0.7rem] text-zinc-500">
                    <span
                      className={`rounded px-1.5 py-0.5 font-medium ${STATUS_STYLE[document.status]}`}
                    >
                      {STATUS_TEXT[document.status]}
                    </span>
                    {isReady && (
                      <span>
                        {document.chunk_count} chunks
                        {document.page_count > 0 && ` · ${document.page_count} pages`}
                      </span>
                    )}
                    <span>{formatBytes(document.size_bytes)}</span>
                  </p>

                  {document.error && (
                    <p className="mt-1.5 text-[0.7rem] leading-snug text-red-400">
                      {document.error}
                    </p>
                  )}
                </div>

                {confirming === document.document_id ? (
                  <div className="flex shrink-0 gap-1 text-[0.7rem]">
                    <button
                      type="button"
                      onClick={() => {
                        void state.remove(document.document_id);
                        setConfirming(null);
                      }}
                      className="rounded bg-red-600/80 px-2 py-1 font-medium text-white hover:bg-red-600"
                    >
                      Delete
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirming(null)}
                      className="rounded border border-zinc-700 px-2 py-1 text-zinc-300 hover:bg-zinc-800"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setConfirming(document.document_id)}
                    aria-label={`Delete ${document.filename}`}
                    className="shrink-0 rounded p-1 text-zinc-600 transition-colors hover:bg-zinc-800 hover:text-red-400"
                  >
                    ✕
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      <p className="text-[0.7rem] leading-snug text-zinc-600">
        {selectedIds.length === 0
          ? "Questions search every indexed document."
          : `Questions search ${selectedIds.length} selected ${selectedIds.length === 1 ? "document" : "documents"}.`}
      </p>
    </div>
  );
}
