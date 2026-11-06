/**
 * lib/api.ts — The single place the browser talks to the DocuMind backend.
 *
 * Every fetch in the app goes through here so that auth headers, error unwrapping, and the SSE decoding
 * live in one reviewable file instead of being re-implemented per component. Identity itself lives in
 * lib/session.ts; this file only asks it for a header.
 */

import { API_URL } from "./config";
import { authHeaders, handleUnauthorized } from "./session";

export { API_URL };

export type DocumentStatus = "uploading" | "parsing" | "indexed" | "failed";

export type DocumentRecord = {
  document_id: string;
  owner_id: string;
  filename: string;
  content_type: string;
  status: DocumentStatus;
  page_count: number;
  chunk_count: number;
  size_bytes: number;
  uploaded_at: string;
  error: string | null;
};

export type UploadAccepted = {
  documents: DocumentRecord[];
  detail: string;
};

export type Citation = {
  index: number;
  document_id: string;
  filename: string;
  chunk_id: string;
  page_number: number;
  snippet: string;
};

export type ConversationSummary = {
  conversation_id: string;
  title: string;
  updated_at: string;
};

export type ChatMessage = {
  role: string;
  content: string;
  citations: Citation[];
  agent_trace: string[];
  created_at: string;
};

export type ConversationDetail = {
  conversation: ConversationSummary;
  messages: ChatMessage[];
};

/** A response that carries a readable reason, so the UI can show the backend's message verbatim. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function headers(): HeadersInit {
  return authHeaders();
}

/**
 * One 401 anywhere means the stored token is worthless — expired, or issued by a server that restarted with
 * a different secret. Clearing it here puts the whole app back to the sign-in panel instead of leaving every
 * leaving every panel to fail on its own.
 */
function noteIfRejected(status: number): void {
  if (status === 401) handleUnauthorized();
}

/** Unwrap FastAPI's error body, which is `detail` for HTTPException and a list for validation errors. */
async function errorFrom(response: Response): Promise<ApiError> {
  let message = `Request failed with status ${response.status}`;
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === "string") message = detail;
    else if (Array.isArray(detail) && detail.length > 0)
      // pydantic validation errors: name the field, not the whole object dump.
      message = `${detail[0]?.loc?.slice(1).join(".") ?? "request"}: ${detail[0]?.msg ?? "invalid"}`;
    else if (detail) message = JSON.stringify(detail);
  } catch {
    // A non-JSON error page (a proxy timeout, usually) keeps the status-based message.
  }
  return new ApiError(response.status, message);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { ...headers(), ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    noteIfRejected(response.status);
    throw await errorFrom(response);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function listDocuments(signal?: AbortSignal): Promise<DocumentRecord[]> {
  return request<DocumentRecord[]>("/api/documents", { signal });
}

export function listConversations(
  signal?: AbortSignal,
): Promise<ConversationSummary[]> {
  return request<ConversationSummary[]>("/api/conversations", { signal });
}

export function getConversation(
  conversationId: string,
  signal?: AbortSignal,
): Promise<ConversationDetail> {
  return request<ConversationDetail>(`/api/conversations/${conversationId}`, {
    signal,
  });
}

/**
 * Multipart upload. One request for the whole selection: the backend accepts a list and indexes each
 * file in its own background task, so a five-file drop is one round trip rather than five.
 */
export function uploadFiles(
  files: File[],
  signal?: AbortSignal,
): Promise<UploadAccepted> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  // No Content-Type header: the browser has to set the multipart boundary itself.
  return request<UploadAccepted>("/api/upload", { method: "POST", body: form, signal });
}

export function deleteDocument(documentId: string): Promise<void> {
  return request<void>(`/api/documents/${documentId}`, { method: "DELETE" });
}

export type ChatRequest = {
  question: string;
  conversation_id?: string;
  document_ids?: string[];
  top_k?: number;
};

/** One decoded Server-Sent Event. `data` is the parsed JSON payload of the frame. */
export type StreamEvent = { event: string; data: unknown };

/**
 * POST /api/chat and hand each SSE frame to `onEvent` as it arrives.
 *
 * EventSource is not usable here: it only issues GET, and the question has to travel in a body. So the
 * body stream is read manually and split on the blank line that terminates a frame. Frames can arrive
 * split across network chunks — a chunk boundary in the middle of `data:` is normal — hence the buffer
 * rather than per-chunk parsing.
 */
export async function streamChat(
  payload: ChatRequest,
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_URL}/api/chat`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    noteIfRejected(response.status);
    throw await errorFrom(response);
  }
  if (!response.body) throw new ApiError(500, "The browser received no response body.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");

      const decoded = decodeFrame(frame);
      if (decoded) onEvent(decoded);
    }
  }

  // A trailing frame without its terminator would otherwise be dropped. The server always terminates,
  // so this only fires if a proxy cut the stream early — which is worth surfacing, not swallowing.
  const leftover = decodeFrame(buffer);
  if (leftover) onEvent(leftover);
}

function decodeFrame(frame: string): StreamEvent | null {
  const lines = frame.split("\n");
  let event = "message";
  let data = "";
  for (const line of lines) {
    if (line.startsWith("event: ")) event = line.slice(7).trim();
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!data) return null;
  try {
    return { event, data: JSON.parse(data) };
  } catch {
    return null;
  }
}
