"use client";

/**
 * lib/use-documents.ts — Document list state: fetch, upload, delete, and status polling.
 *
 * Upload returns 202 with the record still `uploading`, because indexing (extract → chunk → embed →
 * upsert) runs in a FastAPI background task. The client therefore has to ask again until the status
 * settles, and this hook owns that loop so no component has to remember to start or stop it.
 *
 * The interval is derived from the data rather than started by the caller: a component that began
 * polling on upload and stopped on unmount would leave a re-uploaded file stale after a failed ingest.
 */

import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  deleteDocument,
  type DocumentRecord,
  listDocuments,
  uploadFiles,
} from "./api";

const POLL_INTERVAL_MS = 2000;

function isPending(document: DocumentRecord): boolean {
  return document.status === "uploading" || document.status === "parsing";
}

export type DocumentsState = {
  documents: DocumentRecord[];
  loading: boolean;
  error: string | null;
  uploading: boolean;
  refresh: () => void;
  upload: (files: File[]) => Promise<void>;
  remove: (documentId: string) => Promise<void>;
};

export function useDocuments(): DocumentsState {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();

    // This runs in an effect rather than during render because the request reads the demo key from
    // localStorage, which does not exist on the server.
    listDocuments(controller.signal)
      .then((rows) => {
        if (controller.signal.aborted) return;
        setDocuments(rows);
        setError(null);
        setLoading(false);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(describe(cause));
        setLoading(false);
      });

    return () => controller.abort();
  }, [refreshKey]);

  // Poll only while something is still being indexed; an always-on interval would keep hitting the API
  // on an idle tab.
  const anyPending = documents.some(isPending);
  useEffect(() => {
    if (!anyPending) return;
    const timer = window.setInterval(
      () => setRefreshKey((key) => key + 1),
      POLL_INTERVAL_MS,
    );
    return () => window.clearInterval(timer);
  }, [anyPending]);

  const refresh = useCallback(() => setRefreshKey((key) => key + 1), []);

  const upload = useCallback(async (files: File[]) => {
    if (files.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      await uploadFiles(files);
      // Refresh immediately: the new rows come back as `uploading` and the poll above takes over.
      setRefreshKey((key) => key + 1);
    } catch (cause: unknown) {
      setError(describe(cause));
    } finally {
      setUploading(false);
    }
  }, []);

  const remove = useCallback(async (documentId: string) => {
    setError(null);
    try {
      await deleteDocument(documentId);
      setDocuments((rows) => rows.filter((row) => row.document_id !== documentId));
    } catch (cause: unknown) {
      setError(describe(cause));
    }
  }, []);

  return { documents, loading, error, uploading, refresh, upload, remove };
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError) return cause.message;
  if (cause instanceof Error && cause.name === "AbortError") return "";
  if (cause instanceof Error) return cause.message;
  return "Something went wrong.";
}
