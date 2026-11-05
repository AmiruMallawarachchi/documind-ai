"use client";

/**
 * components/AccessGate.tsx — The one screen between a visitor and the demo.
 *
 * The access code is published in the README, so this gate is not a secret and is not pretending to be
 * one. It exists so that the Render URL is not an anonymous spillover bucket for anyone who finds it, and
 * so the owner id that scopes every document and conversation comes from a signature rather than from a
 * header a caller typed.
 *
 * The code is held in local state and never written to storage: reloading the page should not leave a
 * usable credential sitting in the DOM or in localStorage for whoever uses the browser next.
 */

import { useState } from "react";

type Props = {
  submit: (accessCode: string) => Promise<string | null>;
};

export default function AccessGate({ submit }: Props) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const open = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = code.trim();
    if (!trimmed || busy) return;

    setBusy(true);
    setError(null);
    // Reaching the server can take a minute: Render's free tier sleeps after 15 minutes of quiet, and the
    // first request is the one that wakes it. The button says so rather than looking broken.
    const message = await submit(trimmed);
    if (message) setError(message);
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

        <form onSubmit={open} className="space-y-3">
          <label htmlFor="access-code" className="block text-[0.7rem] font-semibold uppercase tracking-widest text-zinc-500">
            Demo access code
          </label>
          <input
            id="access-code"
            type="password"
            autoComplete="off"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "access-error" : undefined}
            placeholder="Type the code from the project README"
            className="w-full rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2.5 text-sm placeholder:text-zinc-600 focus:border-indigo-500 focus:outline-none"
          />
          <button
            type="submit"
            disabled={busy || code.trim().length === 0}
            className="w-full rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-600"
          >
            {busy ? "Opening the workspace…" : "Open workspace"}
          </button>
        </form>

        {error && (
          <p id="access-error" role="alert" className="rounded-lg border border-red-900/60 bg-red-950/40 px-3 py-2 text-xs text-red-300">
            {error}
          </p>
        )}

        <p className="text-[0.7rem] leading-relaxed text-zinc-600">
          Your documents and conversations stay scoped to this browser. The server hands back a session
          token that lasts a day; sign in again after that. If this is the first visit in a while, the
          demo server may take about a minute to wake up.
        </p>
      </div>
    </main>
  );
}
