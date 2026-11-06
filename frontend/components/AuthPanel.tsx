"use client";

/**
 * components/AuthPanel.tsx — The screen between a visitor and an account.
 *
 * Two modes, one form. Sign in and create an account ask for the same two fields, so they share the inputs,
 * the labels, the error slot and the busy state, and differ only in which endpoint the submit button calls
 * and what the panel says afterwards.
 *
 * The password never leaves this component's state. It is sent once, and on a failure it stays in the box
 * because a wrong *email* is the common mistake and making someone retype both is a worse guess than
 * letting them fix the one field that was actually wrong. The eye button changes only how the field is
 * rendered, never what is in it, and it is a `type="button"` so revealing cannot submit the form.
 *
 * While a request is in flight the submit button keeps its normal colour and gains a spinner. Grey text on
 * an indigo button reads as "this control is disabled because something is wrong"; the something that is
 * actually true is that the server has not answered yet.
 *
 * What the backend refuses to say shapes what this can say. Sign-up answers one sentence whether or not the
 * address already exists, so there is no "that email is taken" branch to draw, and a failed sign-in answers
 * the same words for an unknown address and a wrong password — the panel shows them and offers no theory.
 * The 403 is the exception the backend makes deliberately, and it is the only refusal here that comes with
 * a button.
 */

import { useState } from "react";

import {
  createAccount,
  resendVerification,
  signIn,
  storedEmail,
  type AckOutcome,
} from "@/lib/session";

type Mode = "signin" | "signup";

export default function AuthPanel() {
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState(() => storedEmail());
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** The server's own sentence, for the flows that succeed without minting a session. */
  const [notice, setNotice] = useState<string | null>(null);
  /** Set by a 403: an account exists, the address does not, and one click can fix it. */
  const [unverified, setUnverified] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const switchMode = (next: Mode) => {
    setMode(next);
    setError(null);
    setNotice(null);
    setUnverified(null);
  };

  const report = (result: AckOutcome) => {
    if ("error" in result) {
      setError(result.error);
      setNotice(null);
    } else {
      setNotice(result.message);
      setError(null);
    }
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const address = email.trim();
    if (busy || !address || password.length === 0) return;

    setBusy(true);
    setError(null);
    setNotice(null);
    setUnverified(null);

    if (mode === "signup") {
      report(await createAccount(address, password));
    } else {
      const result = await signIn(address, password);
      // A session needs no handling here: publishing it notifies the store and `Home` mounts the workspace.
      if ("unverified" in result) {
        setUnverified(result.unverified);
      } else if ("error" in result) {
        setError(result.error);
      }
    }
    setBusy(false);
  };

  const resend = async () => {
    if (!unverified || busy) return;
    setBusy(true);
    const target = unverified;
    setUnverified(null);
    report(await resendVerification(target));
    setBusy(false);
  };

  const label = "block text-[0.7rem] font-semibold uppercase tracking-widest text-zinc-500";
  const field =
    "w-full rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2.5 text-sm placeholder:text-zinc-600 focus:border-indigo-500 focus:outline-none";
  // A disabled submit keeps its normal colour: the grey variant read as "this control is broken", and the
  // only thing that is actually true while `busy` is that the request has not come back yet.
  const button =
    "flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed";

  return (
    <main className="flex min-h-dvh items-center justify-center bg-zinc-950 px-4 py-10 text-zinc-100">
      <div className="w-full max-w-sm space-y-6">
        <header className="space-y-2 text-center">
          <span
            className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl bg-indigo-600 text-lg font-bold"
            aria-hidden
          >
            D
          </span>
          <h1 className="text-xl font-semibold tracking-tight">DocuMind AI</h1>
          <p className="text-sm text-zinc-400">
            Multi-agent document Q&amp;A — answers checked against your sources before you see them.
          </p>
        </header>

        <div className="flex rounded-xl border border-zinc-800 bg-zinc-900/60 p-1 text-xs font-semibold">
          {(["signin", "signup"] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => switchMode(option)}
              aria-pressed={mode === option}
              className={`flex-1 rounded-lg px-3 py-2 transition-colors ${
                mode === option ? "bg-indigo-600 text-white" : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {option === "signin" ? "Sign in" : "Create account"}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1.5">
            <label htmlFor="email" className={label}>
              Email
            </label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "auth-error" : undefined}
              placeholder="you@example.com"
              className={field}
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="password" className={label}>
              Password
            </label>
            <div className="relative">
              <input
                id="password"
                type={reveal ? "text" : "password"}
                // current-password on sign in, new-password on sign up: a manager that offered the stored
                // password on the create-account form would fill the field it is meant to replace.
                autoComplete={mode === "signin" ? "current-password" : "new-password"}
                required
                minLength={mode === "signup" ? 12 : undefined}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "auth-error" : undefined}
                placeholder={mode === "signin" ? "Your password" : "At least 12 characters"}
                className={`${field} pr-11`}
              />
              <button
                type="button"
                onClick={() => setReveal((current) => !current)}
                aria-label={reveal ? "Hide the password" : "Show the password"}
                aria-pressed={reveal}
                aria-controls="password"
                className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-zinc-500 transition-colors hover:text-zinc-200 focus:outline-none focus-visible:text-indigo-400"
              >
                {reveal ? <EyeOff /> : <Eye />}
              </button>
            </div>
          </div>

          <button
            type="submit"
            disabled={busy || email.trim().length === 0 || password.length === 0}
            aria-busy={busy}
            className={button}
          >
            {busy && (
              <svg
                className="h-4 w-4 animate-spin"
                viewBox="0 0 24 24"
                fill="none"
                aria-hidden
              >
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z" />
              </svg>
            )}
            {mode === "signin" ? "Open workspace" : "Create account and send the link"}
          </button>
        </form>

        {error && (
          <p id="auth-error" role="alert" className="rounded-lg border border-red-900/60 bg-red-950/40 px-3 py-2 text-xs text-red-300">
            {error}
          </p>
        )}

        {notice && (
          <p className="rounded-lg border border-indigo-900/60 bg-indigo-950/40 px-3 py-2 text-xs leading-relaxed text-indigo-200">
            {notice}
          </p>
        )}

        {unverified && (
          <div className="space-y-2 rounded-lg border border-amber-900/60 bg-amber-950/30 px-3 py-2.5 text-xs text-amber-200">
            <p>
              {unverified} has an account but has not confirmed the address yet, so the password does not
              open it.
            </p>
            <button
              type="button"
              onClick={resend}
              disabled={busy}
              className="rounded-lg border border-amber-800 px-2.5 py-1 font-semibold transition-colors hover:border-amber-600 disabled:opacity-60"
            >
              Send the confirmation link again
            </button>
          </div>
        )}

        <p className="text-[0.7rem] leading-relaxed text-zinc-600">
          {mode === "signup"
            ? "Confirmation links work once and stay valid for a day. Your documents and conversations belong to your address, not to this browser, so signing in somewhere else finds them."
            : "Your session token lasts a day; sign in again after that. If this is the first visit in a while, the demo server may take a minute and a half to wake up."}
        </p>
      </div>
    </main>
  );
}

/** Two strokes, so the icon can be recoloured by the button that owns it and needs no icon library. */
function Eye() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function EyeOff() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M2 12s3.6-7 10-7c2 0 3.8.6 5.3 1.5" />
      <path d="M18.6 6.9C20.7 8.5 22 12 22 12s-3.6 7-10 7c-2 0-3.8-.6-5.3-1.5" />
      <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
      <path d="m3 3 18 18" />
    </svg>
  );
}
