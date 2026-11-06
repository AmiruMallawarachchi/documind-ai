"use client";

/**
 * lib/use-session.ts — The React half of authentication.
 *
 * Owns exactly one question: is there a usable token in this browser right now? Everything else — storage,
 * the four calls to the auth endpoints, the header — lives in lib/session.ts, which is the store this reads
 * through `useSyncExternalStore`. That hook is what lets a page rendered without localStorage settle on the
 * real answer after hydration without a setState-in-effect to cause a second render pass.
 *
 * It no longer exposes a `submit`. The sign-in form is the only thing that submits anything, it needs the
 * field state, the busy flag and the 403 branch alongside it, and threading all three through a hook that
 * two other panels use bought a wider interface for nobody.
 */

import { useCallback, useSyncExternalStore } from "react";

import {
  getSessionServerSnapshot,
  getSessionSnapshot,
  SESSION_PENDING,
  subscribeSession,
  publishSession,
  type Session,
} from "./session";

export type SessionState = {
  /** Null until `ready`, and thereafter the live session. */
  session: Session | null;
  /** False only while the server snapshot is still standing in for the browser's. */
  ready: boolean;
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

  const signOut = useCallback(() => publishSession(null), []);

  return { session, ready, signOut };
}
