"use client";

/**
 * lib/use-session.ts — The React half of the access-code gate.
 *
 * Owns exactly one question: is there a usable token in this browser right now? Everything else — storage,
 * the exchange with the server, the header — lives in lib/session.ts, which is the store this reads through
 * `useSyncExternalStore`. That hook is what lets a page rendered without localStorage settle on the real
 * answer after hydration without a setState-in-effect to cause a second render pass.
 */

import { useCallback, useSyncExternalStore } from "react";

import {
  getSessionServerSnapshot,
  getSessionSnapshot,
  SESSION_PENDING,
  signInWithAccessCode,
  subscribeSession,
  publishSession,
  type Session,
} from "./session";

export type SessionState = {
  /** Null until `ready`, and thereafter the live session. */
  session: Session | null;
  /** False only while the server snapshot is still standing in for the browser's. */
  ready: boolean;
  /** Resolves to an error message to show, or null when the workspace may open. */
  submit: (accessCode: string) => Promise<string | null>;
  signOut: () => void;
};

export function useSession(): SessionState {
  const snapshot = useSyncExternalStore(
    subscribeSession,
    getSessionSnapshot,
    getSessionServerSnapshot,
  );

  const ready = snapshot !== SESSION_PENDING;
  const session = ready ? snapshot : null;

  const submit = useCallback(async (accessCode: string) => {
    const result = await signInWithAccessCode(accessCode);
    if ("error" in result) return result.error;
    return null;
  }, []);

  const signOut = useCallback(() => publishSession(null), []);

  return { session, ready, submit, signOut };
}
