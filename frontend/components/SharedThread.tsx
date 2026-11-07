"use client";

/**
 * components/SharedThread.tsx — Fetching the thread a token names, and saying what came back.
 *
 * No session is involved, because a recipient does not have one: `getSharedConversation` is the one call in
 * `lib/api.ts` that sends no `Authorization` header, and this is the component that makes it.
 *
 * Three endings, and the two failures are not the same sentence. A `404` is a link that has been revoked or
 * never existed, which is a fact about the link and worth stating plainly; anything else — a cold backend,
 * a refused proxy, the address rate limit — is a fact about the moment, and saying "this link is dead" about
 * a server that is merely waking up would send the reader away from a page that works a minute later.
 *
 * The token comes from the router, not from a prop: the route that renders this is a static shell and cannot
 * know it at build time. So the first render has nothing to ask for, and the effect waits for one — fetching
 * an empty token would 404 and tell a reader with a good link that it was a bad one. A token that changes
 * under a mounted component re-runs the effect against the new value; the old request is aborted, so only
 * the answer to the current link can be drawn.
 */

import { useEffect, useState } from "react";

import { useParams } from "next/navigation";

import SharedTranscript from "@/components/SharedTranscript";
import EmptyNote from "@/components/ui/EmptyNote";
import { ApiError, getSharedConversation, type SharedConversation } from "@/lib/api";

type State =
  | { phase: "working" }
  | { phase: "ready"; thread: SharedConversation }
  | { phase: "gone" }
  | { phase: "unreachable"; message: string };

export default function SharedThread() {
  const params = useParams<{ token: string }>();
  const token = params?.token;
  const [state, setState] = useState<State>({ phase: "working" });

  useEffect(() => {
    if (!token) return;

    const controller = new AbortController();

    getSharedConversation(token, controller.signal)
      .then((thread) => setState({ phase: "ready", thread }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        // The body is not read after an abort, so an abort that races the unmount stays silent.
        if (error instanceof ApiError && error.status === 404) {
          setState({ phase: "gone" });
          return;
        }
        setState({
          phase: "unreachable",
          message:
            error instanceof ApiError && error.status !== 500
              ? error.message
              : "DocuMind did not answer. Try again in a moment.",
        });
      });

    return () => controller.abort();
  }, [token]);

  if (state.phase === "ready") {
    return (
      <SharedTranscript
        title={state.thread.title}
        updatedAt={state.thread.updated_at}
        messages={state.thread.messages}
      />
    );
  }

  if (state.phase === "gone") {
    return (
      <EmptyNote>
        This link does not lead anywhere. Either the person who shared it took the link back, or it was
        mistyped — the thread itself is still theirs.
      </EmptyNote>
    );
  }

  if (state.phase === "unreachable") {
    return <EmptyNote>{state.message}</EmptyNote>;
  }

  return <EmptyNote>Loading this thread…</EmptyNote>;
}
