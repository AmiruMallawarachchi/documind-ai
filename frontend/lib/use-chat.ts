"use client";

/**
 * lib/use-chat.ts — One conversation's transcript and the streaming turn that extends it.
 *
 * The whole SSE protocol is reduced into a single list of UiMessage values here, because the components
 * should render state and never own transport. Each frame type maps to one obvious mutation:
 *   status    → the label above the input ("Checking the answer against your sources")
 *   meta      → the conversation id, which the new-thread flow does not have until the server answers
 *   citations → chips under the answer
 *   token     → text appended to the assistant message
 *   done      → the message is marked complete and the trace is stored with it
 *   error     → the message becomes a retry affordance, not a dead end
 *
 * Effect cleanup aborts the in-flight stream. Under Cache Components the router can hide and re-show a
 * route without unmounting the page, so "the effect ran exactly once" is not an assumption to lean on —
 * the controller is created per ask() call rather than once per mount.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  type ChatMessage,
  type Citation,
  getConversation,
  streamChat,
} from "./api";

export type UiMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  agentTrace: string[];
  /** Streaming is still writing this message. */
  pending: boolean;
  /** Set when the turn failed; the UI offers a retry instead of an empty bubble. */
  error: string | null;
};

let messageSequence = 0;

function nextId(): string {
  messageSequence += 1;
  return `m${messageSequence}`;
}

function fromStored(row: ChatMessage): UiMessage {
  return {
    id: nextId(),
    role: row.role === "user" ? "user" : "assistant",
    content: row.content,
    citations: row.citations ?? [],
    agentTrace: row.agent_trace ?? [],
    pending: false,
    error: null,
  };
}

export type ChatState = {
  messages: UiMessage[];
  stage: string | null;
  busy: boolean;
  error: string | null;
  ask: (question: string, documentIds: string[]) => void;
  stop: () => void;
  clearError: () => void;
};

export function useChat(
  conversationId: string | null,
  onConversationCreated: (conversationId: string) => void,
): ChatState {
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [stage, setStage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const controller = useRef<AbortController | null>(null);
  // ask() updates this the moment the server issues a new thread's id, because the question that
  // created the thread has to be followed up against it before the parent's prop reaches this render.
  const currentId = useRef<string | null>(conversationId);
  // Which conversation the transcript in state came from. Setting it as soon as the id is adopted is what
  // stops the handoff below from re-fetching an empty thread over the live transcript.
  const loadedId = useRef<string | null>(null);
  // The id of the assistant message being written, so token frames patch one row instead of searching.
  const liveId = useRef<string | null>(null);

  // Loading a saved thread replaces the transcript. Reading it is what makes a refresh, or a click on an
  // older conversation, show the whole exchange instead of an empty box.
  //
  // Switching away from a thread is handled by the parent remounting this hook under a new key, which is
  // React's own answer to "state should reset when this changes" and why no branch here writes state
  // synchronously.
  useEffect(() => {
    if (!conversationId) return;
    if (loadedId.current === conversationId) return;

    const local = new AbortController();
    getConversation(conversationId, local.signal)
      .then((detail) => {
        if (local.signal.aborted) return;
        loadedId.current = conversationId;
        setMessages(detail.messages.map(fromStored));
      })
      .catch((cause: unknown) => setError(describe(cause)));
    return () => local.abort();
  }, [conversationId]);

  const patch = useCallback((id: string, changes: Partial<UiMessage>) => {
    setMessages((rows) =>
      rows.map((row) => (row.id === id ? { ...row, ...changes } : row)),
    );
  }, []);

  const ask = useCallback(
    (question: string, documentIds: string[]) => {
      const trimmed = question.trim();
      if (!trimmed || busy) return;

      setError(null);
      setBusy(true);
      setStage(null);

      const assistantId = nextId();
      liveId.current = assistantId;
      setMessages((rows) => [
        ...rows,
        { id: nextId(), role: "user", content: trimmed, citations: [], agentTrace: [], pending: false, error: null },
        { id: assistantId, role: "assistant", content: "", citations: [], agentTrace: [], pending: true, error: null },
      ]);

      const local = new AbortController();
      controller.current = local;

      streamChat(
        {
          question: trimmed,
          conversation_id: currentId.current ?? undefined,
          document_ids: documentIds.length > 0 ? documentIds : undefined,
        },
        ({ event, data }) => {
          const payload = (data ?? {}) as Record<string, unknown>;

          if (event === "status") {
            setStage(String(payload.label ?? ""));
            return;
          }
          if (event === "meta") {
            const created = payload.conversation_id;
            if (typeof created === "string" && !currentId.current) {
              currentId.current = created;
              // This transcript is already on screen; marking it loaded stops the id handoff from
              // re-fetching an empty thread from Mongo.
              loadedId.current = created;
              onConversationCreated(created);
            }
            return;
          }
          if (event === "citations") {
            patch(assistantId, { citations: (payload as unknown as Citation[]) ?? [] });
            return;
          }
          if (event === "token") {
            const text = String(payload.text ?? "");
            setMessages((rows) =>
              rows.map((row) =>
                row.id === assistantId ? { ...row, content: row.content + text } : row,
              ),
            );
            return;
          }
          if (event === "done") {
            // The server's authoritative text wins over the accumulated tokens: they should be equal, and
            // if a proxy dropped a frame this is what keeps the displayed answer matching what was stored.
            patch(assistantId, {
              content: String(payload.answer ?? ""),
              citations: (payload.citations as Citation[]) ?? [],
              agentTrace: (payload.agent_trace as string[]) ?? [],
              pending: false,
            });
            return;
          }
          if (event === "error") {
            patch(assistantId, {
              pending: false,
              error: String(payload.message ?? "The answer could not be completed."),
            });
          }
        },
        local.signal,
      )
        .catch((cause: unknown) => {
          if (local.signal.aborted) return;
          const message = describe(cause);
          if (message) setError(message);
          patch(assistantId, { pending: false, error: message || "The answer could not be completed." });
        })
        .finally(() => {
          if (controller.current === local) controller.current = null;
          setBusy(false);
          setStage(null);
        });
    },
    [busy, onConversationCreated, patch],
  );

  const stop = useCallback(() => {
    controller.current?.abort();
    const id = liveId.current;
    if (id) patch(id, { pending: false });
    setBusy(false);
    setStage(null);
  }, [patch]);

  const clearError = useCallback(() => setError(null), []);

  // A stream that outlives the panel would keep appending to state that no longer renders.
  useEffect(() => () => controller.current?.abort(), []);

  return { messages, stage, busy, error, ask, stop, clearError };
}

function describe(cause: unknown): string {
  if (cause instanceof ApiError) return cause.message;
  if (cause instanceof Error && cause.name === "AbortError") return "";
  if (cause instanceof Error) return cause.message;
  return "Something went wrong.";
}
