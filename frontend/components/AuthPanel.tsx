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
 * While a request is in flight the submit button keeps its normal colour and gains a spinner. That rule
 * lives in `ui/Button`'s `busy` prop, and it is here because this is the panel that taught it to me: grey
 * text on an indigo button reads as "this control is disabled because something is wrong", and the
 * something that is actually true is that the server has not answered yet.
 *
 * What the backend refuses to say shapes what this can say. Sign-up answers one sentence whether or not the
 * address already exists, so there is no "that email is taken" branch to draw, and a failed sign-in answers
 * the same words for an unknown address and a wrong password — the panel shows them and offers no theory.
 * The 403 is the exception the backend makes deliberately, and it is the only refusal here that comes with
 * a button.
 */

import { useState } from "react";

import Banner from "@/components/ui/Banner";
import Button from "@/components/ui/Button";
import { EyeIcon, EyeOffIcon } from "@/components/ui/icons";
import { FIELD, FIELD_LABEL, FOCUS, HINT } from "@/components/ui/tokens";
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
              className={`flex-1 rounded-lg px-3 py-2 transition-colors ${FOCUS} ${
                mode === option ? "bg-indigo-600 text-white" : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {option === "signin" ? "Sign in" : "Create account"}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1.5">
            <label htmlFor="email" className={FIELD_LABEL}>
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
              className={FIELD}
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="password" className={FIELD_LABEL}>
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
                className={`${FIELD} pr-11`}
              />
              <button
                type="button"
                onClick={() => setReveal((current) => !current)}
                aria-label={reveal ? "Hide the password" : "Show the password"}
                aria-pressed={reveal}
                aria-controls="password"
                className={`absolute inset-y-0 right-0 flex w-11 items-center justify-center text-zinc-500 transition-colors hover:text-zinc-200 ${FOCUS}`}
              >
                {reveal ? <EyeOffIcon /> : <EyeIcon />}
              </button>
            </div>
          </div>

          <Button
            type="submit"
            block
            busy={busy}
            disabled={busy || email.trim().length === 0 || password.length === 0}
          >
            {mode === "signin" ? "Open workspace" : "Create account and send the link"}
          </Button>
        </form>

        {error && (
          <Banner id="auth-error" role="alert">
            {error}
          </Banner>
        )}

        {notice && <Banner tone="info">{notice}</Banner>}

        {unverified && (
          <Banner tone="warning" className="space-y-2">
            <p>
              {unverified} has an account but has not confirmed the address yet, so the password does not
              open it.
            </p>
            <Button variant="warning" size="xs" onClick={resend} disabled={busy}>
              Send the confirmation link again
            </Button>
          </Banner>
        )}

        <p className={HINT}>
          {mode === "signup"
            ? "Confirmation links work once and stay valid for a day. Your documents and conversations belong to your address, not to this browser, so signing in somewhere else finds them."
            : "Your session token lasts a day; sign in again after that. If this is the first visit in a while, the demo server may take a minute and a half to wake up."}
        </p>
      </div>
    </main>
  );
}
