/**
 * lib/session.ts — The browser half of authentication, as a subscribable store.
 *
 * An account is an email, a password, and a link the owner clicked. This module is the client side of all
 * three: it calls the four endpoints in `routers/auth.py`, keeps the issued token in localStorage, and
 * gives every call in lib/api.ts its `Authorization` header.
 *
 * It is a store rather than a hook on purpose. The token lives in localStorage, which does not exist during
 * the server render, so React needs an external source it can hydrate against — `useSyncExternalStore` is
 * how you say "the real value arrives after hydration, and this snapshot is stable until something changes
 * it". Reading localStorage inside `useState` would instead bake the server's guess into the HTML.
 *
 * What is deliberately NOT here: the password. It is read into the form, sent once, and never written to
 * storage — so a shared laptop keeps nobody signed in longer than the token's own day, and an expired
 * session is fixed by typing the password again rather than by a silent refresh that cannot work.
 *
 * The email is stored, which looks inconsistent with that rule and is not. It is not a credential, the
 * browser already knows it from the autofill on any other site, and prefilling it is what turns "sign in
 * again tomorrow" from a typing task into one keystroke.
 */

import { API_URL } from "./config";

const TOKEN_STORAGE = "documind.sessionToken";
const OWNER_STORAGE = "documind.sessionOwner";
const EXPIRES_STORAGE = "documind.sessionExpiresAt";
const EMAIL_STORAGE = "documind.accountEmail";

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

/**
 * What a sign-in attempt produced. `unverified` is split out from `error` because the 403 has a next step
 * — send the link again — and the panel can only offer it if the outcome says which address to send to.
 */
export type SignInOutcome =
  | { session: Session }
  | { unverified: string }
  | { error: string };

/** A plain "here is what happened, in the server's own words" for the two flows that mint no session. */
export type AckOutcome = { message: string } | { error: string };

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
 * The address to prefill the sign-in form with, after a hard reload.
 *
 * Guarded rather than assumed: this is called from a `useState` initialiser, which React also runs while
 * prerendering the HTML on the server, where `localStorage` does not exist.
 */
export function storedEmail(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(EMAIL_STORAGE) ?? "";
}

/** Trimmed and lowercased, because that is the spelling the server keys accounts on. */
function rememberEmail(email: string): void {
  window.localStorage.setItem(EMAIL_STORAGE, email.trim().toLowerCase());
}

/**
 * POST one of the four body-only auth endpoints and describe what came back.
 *
 * Separate from lib/api.ts because none of these calls carry a token — that is what the store exists to
 * obtain — and because their failure shapes (403 with a remedy, 202 with a fixed sentence) are not the
 * "throw an ApiError and let the panel show it" pattern the workspace uses.
 */
async function postAuth<T>(
  path: string,
  body: Record<string, string>,
): Promise<{ ok: true; data: T; status: number } | { ok: false; error: string; status: number }> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    // Not a rejection from the server: it never answered. Render's free tier sleeps after fifteen minutes
    // of quiet and the first request is the one that wakes it, which can take longer than this fetch will
    // wait. Saying so is the difference between "wrong password" and "try again in a minute".
    return {
      ok: false,
      status: 0,
      error: "Could not reach the DocuMind server. It may be waking up — try again in a moment.",
    };
  }

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof payload?.detail === "string" ? payload.detail : `Request failed (${response.status}).`;
    return { ok: false, error: detail, status: response.status };
  }
  return { ok: true, data: payload as T, status: response.status };
}

/** Exchange an email and password for a session token. */
export async function signIn(email: string, password: string): Promise<SignInOutcome> {
  const result = await postAuth<{ access_token: string; owner_id: string; expires_at: string }>(
    "/api/auth/login",
    { email, password },
  );

  if (!result.ok) {
    // 403 is the one refusal with a remedy the app can offer in place. The generic 401 stays a generic
    // message; the backend deliberately does not distinguish a wrong password from an unknown address, and
    // the UI has no business undoing that with a guess.
    if (result.status === 403) return { unverified: email.trim().toLowerCase() };
    return { error: result.error };
  }

  const session: Session = {
    accessToken: String(result.data.access_token),
    // The user id, issued by the server: it is the namespace every document and conversation is scoped to,
    // and it is not something this browser gets to name for itself any more.
    ownerId: String(result.data.owner_id),
    expiresAt: String(result.data.expires_at),
  };
  rememberEmail(email);
  publishSession(session);
  return { session };
}

/**
 * Ask for an account. The answer is the same sentence whether or not the address was already registered,
 * because the endpoint is designed not to say — so this function has no "that email is taken" branch to
 * write, and a duplicate signup looks like a new one from here.
 */
export async function createAccount(email: string, password: string): Promise<AckOutcome> {
  const result = await postAuth<{ message: string }>("/api/auth/register", { email, password });
  if (!result.ok) return { error: result.error };
  // Stored on success only, and only because the very next thing this visitor does is try to sign in —
  // and the answer they get back is a 401 for an address they have just typed once.
  rememberEmail(email);
  return { message: result.data.message };
}

export async function resendVerification(email: string): Promise<AckOutcome> {
  const result = await postAuth<{ message: string }>("/api/auth/resend", { email });
  return result.ok ? { message: result.data.message } : { error: result.error };
}

/**
 * Spend a verification link. The token is the whole request — no email is sent alongside it, so a link
 * cannot be pointed at a different address than the one it was minted for.
 */
export async function verifyEmail(token: string): Promise<AckOutcome> {
  const result = await postAuth<{ email: string }>("/api/auth/verify", { token });
  if (!result.ok) return { error: result.error };
  return { message: `That confirms ${result.data.email}. Your account is ready.` };
}
