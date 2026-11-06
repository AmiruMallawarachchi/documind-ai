"use client";

/**
 * components/DocumentsPanel.tsx — Upload, watch indexing, choose which files a question searches.
 *
 * Indexing is asynchronous, so this panel is a poller's view of the pipeline: it asks for the list, and
 * each file renders itself through `DocumentRow` — `uploading` until the background task finishes, then
 * `indexed` or `failed` with the backend's own error text. The failed case is shown inline rather than as
 * a toast because the reason ("this PDF is scanned, no text layer") is something the user acts on, not
 * something they dismiss.
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

import Banner from "@/components/ui/Banner";
import Button from "@/components/ui/Button";
import DocumentRow from "@/components/DocumentRow";
import EmptyNote from "@/components/ui/EmptyNote";
import { FOCUS, HINT } from "@/components/ui/tokens";
import type { DocumentRecord } from "@/lib/api";
import type { DocumentsState } from "@/lib/use-documents";

const ACCEPTED = ".pdf,.docx,.txt";

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
        <Button
          block
          busy={state.uploading}
          disabled={state.uploading}
          onClick={() => inputRef.current?.click()}
        >
          Upload documents
        </Button>
        <p className={HINT}>
          PDF, DOCX, TXT · up to 10 MB each · drop files anywhere in this panel
        </p>
      </div>

      {state.error && <Banner>{state.error}</Banner>}

      <div className="flex items-center justify-between text-[0.7rem] text-zinc-500">
        <span>
          {indexed.length} ready
          {pending.length > 0 && ` · ${pending.length} indexing`}
        </span>
        {indexed.length > 0 && (
          // Not a `<Button>`: this is a word in the caption row, not a control, and the variant's padding
          // would push it off the right edge the row is flush to.
          <button
            type="button"
            onClick={() =>
              onScopeChange(
                selectedIds.length === indexed.length
                  ? []
                  : indexed.map((row) => row.document_id),
              )
            }
            className={`hover:text-zinc-300 ${FOCUS}`}
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
        {state.loading && <EmptyNote>Loading your documents…</EmptyNote>}

        {!state.loading && state.documents.length === 0 && (
          <EmptyNote>
            Nothing uploaded yet. This account has its own document space, so the list is yours alone.
          </EmptyNote>
        )}

        {state.documents.map((document: DocumentRecord) => (
          <DocumentRow
            key={document.document_id}
            document={document}
            selected={selectedIds.includes(document.document_id)}
            onToggleScope={onToggleScope}
            onRemove={(documentId) => void state.remove(documentId)}
          />
        ))}
      </ul>

      <p className={HINT}>
        {selectedIds.length === 0
          ? "Questions search every indexed document."
          : `Questions search ${selectedIds.length} selected ${selectedIds.length === 1 ? "document" : "documents"}.`}
      </p>
    </div>
  );
}
