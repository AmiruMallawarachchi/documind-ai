"use client";

/**
 * app/page.tsx — The DocuMind workspace: documents on the left, the conversation on the right.
 *
 * This is a client component by choice. The app's data is per-browser, arrives over SSE, and is useless
 * without a session, so server-rendering it would mean fetching nothing and then replacing the whole tree
 * on mount. The shell is static markup (which Next still prerenders into the initial HTML) and the panels
 * fill themselves in with effects.
 *
 * State that two panels need lives here and is passed down; state only one panel needs lives inside it.
 * `useDocuments` and `useChat` are where the actual behaviour lives, and the sidebar's two lists are their
 * own components, which keeps this file about layout.
 *
 * `Home` does one extra thing: it holds the session and decides whether the visitor sees the sign-in panel
 * or the board. `Workspace` is the board, and it only ever mounts with a token already in hand, so none of
 * its effects have to cope with an unauthenticated first render.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import AuthPanel from "@/components/AuthPanel";
import ChatPanel from "@/components/ChatPanel";
import DocumentsPanel from "@/components/DocumentsPanel";
import HistoryList from "@/components/HistoryList";
import Button from "@/components/ui/Button";
import { SECTION_TITLE } from "@/components/ui/tokens";
import {
  API_URL,
  ApiError,
  type ConversationSummary,
  deleteConversation,
  listConversations,
  patchConversation,
  shareConversation,
  unshareConversation,
} from "@/lib/api";
import { useDocuments } from "@/lib/use-documents";
import { useSession } from "@/lib/use-session";

export default function Home() {
  const { session, ready, signOut } = useSession();

  // Neither the panel nor the workspace yet: the stored token has not been read, and guessing wrong in
  // either direction shows a returning visitor a sign-in box they have to dismiss.
  if (!ready) return <div className="min-h-dvh bg-zinc-950" />;
  if (!session) return <AuthPanel />;

  // Keyed by owner so that signing in as a different namespace tears down the previous one's transcripts,
  // document list, and polling timers instead of re-rendering them with the other person's ids still in.
  return <Workspace key={session.ownerId} ownerId={session.ownerId} onSignOut={signOut} />;
}

function Workspace({ ownerId, onSignOut }: { ownerId: string; onSignOut: () => void }) {
  const documents = useDocuments();
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  // Bumping the key remounts the chat panel, which is React's answer to "the transcript should reset
  // when the user picks a different thread" — cleaner than clearing it from an effect. It is separate
  // from conversationId because the server issues a new thread's id mid-stream, and adopting that id
  // must not reset the answer the user is watching arrive.
  const [threadKey, setThreadKey] = useState(0);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [showSidebar, setShowSidebar] = useState(false);

  const refreshConversations = useCallback(() => {
    listConversations()
      .then(setConversations)
      .catch(() => {
        // A missing history list is not worth an error banner: the documents panel already reports
        // connectivity problems, and the conversation itself still works.
      });
  }, []);

  useEffect(() => {
    refreshConversations();
  }, [refreshConversations]);

  const readyIds = useMemo(
    () =>
      new Set(
        documents.documents
          .filter((document) => document.status === "indexed")
          .map((document) => document.document_id),
      ),
    [documents.documents],
  );

  // Derived, not stored: a document that failed or was deleted must not stay in the scope of the next
  // question, or the backend refuses the turn with "these documents are not indexed" for a file the user
  // can no longer see.
  const scope = useMemo(
    () => selectedIds.filter((documentId) => readyIds.has(documentId)),
    [selectedIds, readyIds],
  );

  const indexedCount = readyIds.size;

  const startThread = useCallback((id: string | null) => {
    setConversationId(id);
    setThreadKey((key) => key + 1);
  }, []);

  const onConversationCreated = useCallback(
    (created: string) => {
      // No key bump: this is the id of the thread already on screen.
      setConversationId(created);
      // The title comes from the first question, which the backend has just written — so the list is
      // re-read rather than patched with a guess.
      refreshConversations();
    },
    [refreshConversations],
  );

  /**
   * One write, then a re-read. Patching the local row instead would save a request and cost the truth:
   * the server owns `updated_at`, the `shared` flag, and the order the list comes back in, and a guess
   * here is how a sidebar ends up showing a thread the account does not have.
   */
  const mutateHistory = useCallback(
    async <T,>(write: () => Promise<T>): Promise<T> => {
      setHistoryError(null);
      try {
        // The value is passed back through, not discarded: sharing a thread has to show the row the token
        // the server minted, and this is the only place that knows the write succeeded.
        const result = await write();
        refreshConversations();
        return result;
      } catch (error) {
        setHistoryError(
          error instanceof ApiError ? error.message : "That did not reach the server. Try again.",
        );
        throw error;
      }
    },
    [refreshConversations],
  );

  const renameConversation = useCallback(
    (id: string, title: string) => mutateHistory(() => patchConversation(id, { title })),
    [mutateHistory],
  );

  const pinConversation = useCallback(
    (id: string, pinned: boolean) => mutateHistory(() => patchConversation(id, { pinned })),
    [mutateHistory],
  );

  const shareThread = useCallback(
    (id: string) => mutateHistory(() => shareConversation(id)),
    [mutateHistory],
  );

  const unshareThread = useCallback(
    (id: string) => mutateHistory(() => unshareConversation(id)),
    [mutateHistory],
  );

  const removeConversation = useCallback(
    (id: string) =>
      mutateHistory(() =>
        deleteConversation(id).then(() => {
          // The deleted thread is the one on screen: the transcript it showed is gone, so the panel
          // goes back to a new chat rather than sitting on a thread that now answers 404.
          if (id === conversationId) startThread(null);
        }),
      ),
    [conversationId, mutateHistory, startThread],
  );

  return (
    <main className="flex h-dvh flex-col bg-zinc-950 text-zinc-100">
      <header className="flex items-center gap-3 border-b border-zinc-800 px-4 py-3 sm:px-6">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-sm font-bold">
          D
        </div>
        <div className="min-w-0 flex-1">
          <h1 className="text-sm font-semibold leading-tight">DocuMind AI</h1>
          <p className="truncate text-[0.7rem] text-zinc-500">
            Multi-agent document Q&amp;A · answers verified against your sources
          </p>
        </div>
        <div className="hidden lg:block">
          <Button variant="outline" size="sm" onClick={onSignOut}>
            Sign out
          </Button>
        </div>
        <div className="lg:hidden">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowSidebar((value) => !value)}
            aria-expanded={showSidebar}
          >
            {showSidebar ? "Chat" : "Documents"}
          </Button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 lg:grid-cols-[20rem_1fr]">
        <aside
          className={`${
            showSidebar ? "flex" : "hidden"
          } min-h-0 flex-col gap-4 border-b border-zinc-800 p-4 lg:flex lg:border-b-0 lg:border-r`}
        >
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-hidden">
            <Section title="Documents">
              <DocumentsPanel
                state={documents}
                selectedIds={scope}
                onToggleScope={(documentId) =>
                  setSelectedIds((current) =>
                    current.includes(documentId)
                      ? current.filter((value) => value !== documentId)
                      : [...current, documentId],
                  )
                }
                onScopeChange={setSelectedIds}
              />
            </Section>

            <Section title="History">
              <HistoryList
                conversations={conversations}
                activeId={conversationId}
                error={historyError}
                onSelect={(id) => {
                  startThread(id);
                  setShowSidebar(false);
                }}
                onRename={renameConversation}
                onTogglePin={pinConversation}
                onShare={shareThread}
                onUnshare={unshareThread}
                onDelete={removeConversation}
              />
            </Section>
          </div>

          <div className="flex shrink-0 items-center justify-between gap-2 text-[0.65rem] text-zinc-700">
            <p title={ownerId}>
              API: {API_URL.replace(/^https?:\/\//, "")} · owner {ownerId.slice(0, 8)}
            </p>
            <div className="lg:hidden">
              <Button variant="ghost" size="xs" onClick={onSignOut}>
                Sign out
              </Button>
            </div>
          </div>
        </aside>

        <div className={`${showSidebar ? "hidden" : "flex"} min-h-0 lg:flex`}>
          <ChatPanel
            key={threadKey}
            conversationId={conversationId}
            documentIds={scope}
            indexedCount={indexedCount}
            onConversationCreated={onConversationCreated}
          />
        </div>
      </div>
    </main>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="min-h-0">
      <h2 className={SECTION_TITLE}>{title}</h2>
      {children}
    </section>
  );
}
