/**
 * app/verify/verify-notice.tsx — The card a confirmation link resolves to.
 *
 * Split out of the client component so a Server Component can render it as the `<Suspense>` fallback: the
 * prerendered HTML then already says "Confirming your email…" instead of an empty page, which is the only
 * reason this route has a static shell at all.
 */

import Link from "next/link";

type Props = {
  phase: "working" | "done" | "error";
  message?: string;
};

export default function VerifyNotice({ phase, message }: Props) {
  const title =
    phase === "done" ? "Email confirmed" : phase === "error" ? "That link did not work" : "Confirming your email…";

  return (
    <main className="flex min-h-dvh items-center justify-center bg-zinc-950 px-4 py-10 text-zinc-100">
      <div className="w-full max-w-sm space-y-4 text-center">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>

        {phase === "working" && (
          <p className="text-sm text-zinc-400">One moment — this is the last step before your workspace opens.</p>
        )}

        {message && (
          <p
            role={phase === "error" ? "alert" : undefined}
            className={`rounded-lg border px-3 py-2 text-xs leading-relaxed ${
              phase === "error"
                ? "border-red-900/60 bg-red-950/40 text-red-300"
                : "border-indigo-900/60 bg-indigo-950/40 text-indigo-200"
            }`}
          >
            {message}
          </p>
        )}

        {phase === "error" && (
          <p className="text-[0.7rem] leading-relaxed text-zinc-500">
            A link stops working after the first click or after a day. Sign in with your address and
            password, and the panel will offer to send you a fresh one.
          </p>
        )}

        <Link
          href="/"
          className="inline-block w-full rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-indigo-500"
        >
          {phase === "done" ? "Sign in and start asking" : "Back to sign in"}
        </Link>
      </div>
    </main>
  );
}
