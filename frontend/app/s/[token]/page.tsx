/**
 * app/s/[token]/page.tsx — The page a shared link opens.
 *
 * It has no layout of its own beyond the root one, and that is the point: no sidebar, no document panel,
 * no question box. Everything a visitor could do to this thread is read it, so the route renders a column of
 * prose and hands the rest of the app to people who have their own account.
 *
 * `noindex` is not privacy by obscurity reversed — the token is already unguessable and revocable — it is
 * about not putting someone's business questions into a search engine because a link was pasted in a chat
 * that a crawler followed. `follow: false` says the same about the one link this page does carry, home.
 *
 * This component resolves nothing, and that is a fix rather than a style. It used to `await params` and pass
 * the token down; the served HTML then carried a pending `<!--$?-->` placeholder — the static shell a
 * Cache Components route is allowed to send before its runtime data — and nothing ever filled it. The client
 * did mount, but the boundary stayed suspended on the loading card, so the thread never loaded. Reading the
 * token is a router thing the browser can do for itself, which is how `/verify` has always handled its query
 * string. What stays here is the shell and the fallback, and the fallback is the same card the reader is
 * shown while the fetch is in flight.
 */

import { Suspense } from "react";

import type { Metadata } from "next";

import SharedThread from "@/components/SharedThread";
import EmptyNote from "@/components/ui/EmptyNote";

export const metadata: Metadata = {
  title: "Shared thread · DocuMind AI",
  description:
    "A document Q&A thread shared from DocuMind AI — the answers, with the sources left with their author.",
  robots: { index: false, follow: false },
};

export default function SharedConversationPage() {
  return (
    <main className="flex min-h-dvh justify-center bg-zinc-950 px-4 py-10 text-zinc-100">
      <div className="w-full max-w-2xl">
        <Suspense fallback={<EmptyNote>Loading this thread…</EmptyNote>}>
          <SharedThread />
        </Suspense>
      </div>
    </main>
  );
}
