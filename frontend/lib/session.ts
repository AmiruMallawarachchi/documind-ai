/**
 * lib/session.ts — The browser half of the demo's authentication, as a subscribable store.
 *
 * The visitor types the access code once. This module exchanges it for a session token, keeps it in
 * localStorage, and gives every call in lib/api.ts its `Authorization` header. The owner id the server
 * issued is stored next to it and sent back as `device_key` on the next sign-in, which is what makes a
 * returning visitor see their own documents instead of an empty workspace.
 *
 * It is a store rather than a hook on purpose. The token lives in localStorage, which does not exist during
 * the server render, so React needs an external source it can hydrate against — `useSyncExternalStore` is
 * how you say "the real value arrives after hydration, and this snapshot is stable until something changes
 * it". Reading localStorage inside `useState` would instead bake the server's guess into the HTML.
 *
 * What is deliberately NOT here: the access code. It is never stored, so a shared laptop does not keep the
 * next person signed in, and an expired token cannot be quietly refreshed — the visitor types it again.
 */

import { API_URL } from "./config";

const TOKEN_STORAGE = "documind.sessionToken";
const OWNER_STORAGE = "documind.sessionOwner";
const EXPIRES_STORAGE = "documind.sessionExpiresAt";

export type Session = {
  accessToken: string;
  ownerId: string;
  expiresAt: string;
};

/**
 * The value the server render sees, and the client render sees until the first read completes. Distinct
 * from `null`, which means "read it, and nobody is signed in" — collapsing the two is what makes a
 * returning visitor watch a sign-in box flash before their workspace appears.
 */
export const SESSION_PENDING = "pending";

export type SessionSnapshot = Session | null | typeof SESSION_PENDING;

let snapshot: SessionSnapshot = SESSION_PENDING;
const listeners = new Set<() => void>();

function readStoredSession(): Session | null {
  const accessToken = window.localStorage.getItem(TOKEN_STORAGE);
  const ownerId = window.localStorage.getItem(OWNER_STORAGE);
  const expiresAt = window.localStorage.getItem(EXPIRES_STORAGE);
  if (!accessToken || !ownerId || !expiresAt) return null;

  if (new Date(expiresAt).getTime() <= Date.now()) {
    forget();
    return null;
  }
  return { accessToken, ownerId, expiresAt };
}

function forget(): void {
  window.localStorage.removeItem(TOKEN_STORAGE);
  window.localStorage.removeItem(OWNER_STORAGE);
  window.localStorage.removeItem(EXPIRES_STORAGE);
}

/** Replace the token (or clear it) everywhere at once. Called by sign-in, sign-out, and any 401. */
export function publishSession(next: Session | null): void {
  snapshot = next;
  if (next) {
    window.localStorage.setItem(TOKEN_STORAGE, next.accessToken);
    window.localStorage.setItem(OWNER_STORAGE, next.ownerId);
    window.localStorage.setItem(EXPIRES_STORAGE, next.expiresAt);
  } else {
    forget();
  }
  for (const listener of listeners) listener();
}

export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Must stay referentially stable between changes, or React re-renders forever. */
export function getSessionSnapshot(): SessionSnapshot {
  if (snapshot === SESSION_PENDING) snapshot = readStoredSession();
  return snapshot;
}

export function getSessionServerSnapshot(): SessionSnapshot {
  return SESSION_PENDING;
}

/** The header every authenticated request carries. Empty when there is no token to send. */
export function authHeaders(): Record<string, string> {
  const current = getSessionSnapshot();
  if (!current || current === SESSION_PENDING) return {};
  return { Authorization: `Bearer ${current.accessToken}` };
}

export function handleUnauthorized(): void {
  if (getSessionSnapshot() !== null) publishSession(null);
}

/**
 * Exchange the access code for a token.
 *
 * Returns `{ error }` rather than throwing on a wrong code, because the gate renders the message in the
 * same place as the form and an exception buys nothing there. A network failure is worth naming
 * separately: the code may be perfectly right and the server simply still waking up.
 */
export async function signInWithAccessCode(
  accessCode: string,
): Promise<{ session: Session } | { error: string }> {
  const ownerId = window.localStorage.getItem(OWNER_STORAGE);
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/auth/session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        ownerId ? { access_code: accessCode, device_key: ownerId } : { access_code: accessCode },
      ),
    });
  } catch {
    return { error: "Could not reach the DocuMind server. It may be waking up — try again in a moment." };
  }

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : `Sign-in failed (${response.status}).`;
    return { error: detail };
  }

  const session: Session = {
    accessToken: String(body.access_token),
    ownerId: String(body.owner_id),
    expiresAt: String(body.expires_at),
  };
  publishSession(session);
  return { session };
}
