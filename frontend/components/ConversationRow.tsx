"use client";

/**
 * components/ConversationRow.tsx — One thread in the history list, and the four things it can be told.
 *
 * Rename, pin, share and delete all act on the row they sit in and on nothing else, so each keeps its own
 * open/closed state exactly like `DocumentRow` keeps its confirmation: a panel-level `renamingId` would
 * have made opening a second row's editor close the first, which is behaviour nobody would choose.
 *
 * The title button stays a raw `<button>` — a thread is a full-width, left-aligned, truncating line and
 * its active state is a background, not a variant — but its neighbours are `Button`s, and the glyph that
 * colours them lives on a `<span>` rather than in `className`: a class list cannot reliably replace a
 * variant's same-family utility, because the winner is the order Tailwind emitted them in, not the order
 * they were typed.
 *
 * The share panel is the one place in the sidebar that hands out a credential, so it says what the link
 * means in as many words as it shows the address: *anyone with this can read the answers*.
 */

import { useState } from "react";

import Button from "@/components/ui/Button";
import { CloseIcon, LinkIcon, PencilIcon, PinIcon } from "@/components/ui/icons";
import { FOCUS } from "@/components/ui/tokens";
import { shareUrl, type ConversationSummary } from "@/lib/api";

type Props = {
  conversation: ConversationSummary;
  active: boolean;
  onSelect: (conversationId: string) => void;
  /**
   * The three writes resolve to whatever the server returned, and the row ignores it: `page.tsx` re-reads
   * the list after each one, so the fresh summary is on its way regardless of what this row does with it.
   */
  onRename: (conversationId: string, title: string) => Promise<unknown>;
  onTogglePin: (conversationId: string, pinned: boolean) => Promise<unknown>;
  /** Resolves to the thread's token, which is minted if this is the first time it has been asked for. */
  onShare: (conversationId: string) => Promise<string>;
  onUnshare: (conversationId: string) => Promise<void>;
  onDelete: (conversationId: string) => Promise<void>;
};

export default function ConversationRow({
  conversation,
  active,
  onSelect,
  onRename,
  onTogglePin,
  onShare,
  onUnshare,
  onDelete,
}: Props) {
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(conversation.title);
  const [sharing, setSharing] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const id = conversation.conversation_id;

  /** One flag for everything this row can send: they are all "the server has not answered this row yet",
   *  and a second click while one is in flight would send a rename behind itself — or mint a link the reader
   *  is already waiting for.
   *
   *  The rejection is swallowed here on purpose. `app/page.tsx` owns the message — one line under the
   *  list, because a row for each of four failed writes is worse than one quiet sentence — and it
   *  rethrows precisely so that this row can leave its editor open with the draft still in it. */
  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
    } catch {
      // Left open, unfrozen, and waiting for the next click.
    } finally {
      setBusy(false);
    }
  }

  function startRenaming() {
    setDraft(conversation.title);
    setRenaming(true);
  }

  function stopRenaming() {
    setDraft(conversation.title);
    setRenaming(false);
  }

  function saveTitle() {
    const title = draft.trim();
    if (!title || title === conversation.title) {
      // Nothing to send: the backend refuses an empty title, and an unchanged one costs a round trip to
      // say what the row already knew.
      stopRenaming();
      return;
    }
    void run(() => onRename(id, title).then(stopRenaming));
  }

  function stopSharing() {
    setSharing(false);
    setLink(null);
    setCopied(false);
  }

  /**
   * Opening the panel asks for the link rather than assuming one, because the token is deliberately absent
   * from `GET /api/conversations` — the list is cached and logged, and a share token printed there would be
   * the credential twice over.
   */
  function openShare() {
    setCopied(false);
    setSharing(true);
    void run(async () => setLink(shareUrl(await onShare(id))));
  }

  async function copyLink() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      // The address is in a selectable field precisely so that a refused clipboard is an inconvenience
      // rather than a dead end; no error to report, and nothing to claim.
    }
  }

  return (
    <li className="rounded-lg transition-colors hover:bg-zinc-900/60">
      <div className="flex items-center gap-1 px-1 py-1">
        {renaming ? (
          <form
            className="flex min-w-0 flex-1 items-center gap-1"
            onSubmit={(event) => {
              event.preventDefault();
              saveTitle();
            }}
          >
            <input
              autoFocus
              value={draft}
              maxLength={80}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => event.key === "Escape" && stopRenaming()}
              aria-label={`New title for ${conversation.title}`}
              className={`min-w-0 flex-1 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-100 ${FOCUS}`}
            />
            <Button type="submit" size="xs" busy={busy}>
              Save
            </Button>
            <Button type="button" variant="outline" size="xs" onClick={stopRenaming}>
              Cancel
            </Button>
          </form>
        ) : (
          <>
            <button
              type="button"
              onClick={() => onSelect(id)}
              className={`min-w-0 flex-1 truncate rounded-lg px-2 py-1.5 text-left text-xs transition-colors ${FOCUS} ${
                active ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"
              }`}
              title={conversation.title}
            >
              {conversation.pinned && (
                // A marker, not a control: the Pin button beside it is the reachable one, and its label
                // already says "Unpin", so this only has to be seen, not read.
                <PinIcon
                  className="mr-1 inline-block h-3 w-3 align-[-0.125em] text-indigo-400"
                />
              )}
              {conversation.title}
            </button>

            <div className="flex shrink-0 items-center gap-0.5">
              <Button
                variant="iconNeutral"
                size="xs"
                disabled={busy}
                onClick={() => void run(() => onTogglePin(id, !conversation.pinned))}
                aria-label={`${conversation.pinned ? "Unpin" : "Pin"} ${conversation.title}`}
              >
                <span className={conversation.pinned ? "text-indigo-400" : undefined}>
                  <PinIcon />
                </span>
              </Button>
              <Button
                variant="iconNeutral"
                size="xs"
                disabled={busy}
                onClick={startRenaming}
                aria-label={`Rename ${conversation.title}`}
              >
                <PencilIcon />
              </Button>
              <Button
                variant="iconNeutral"
                size="xs"
                disabled={busy}
                aria-expanded={sharing}
                onClick={() => (sharing ? stopSharing() : openShare())}
                aria-label={`${conversation.shared ? "Show link for" : "Share"} ${conversation.title}`}
              >
                <span className={conversation.shared ? "text-indigo-400" : undefined}>
                  <LinkIcon />
                </span>
              </Button>
              <Button
                variant="icon"
                size="xs"
                disabled={busy}
                onClick={() => setConfirming(true)}
                aria-label={`Delete ${conversation.title}`}
              >
                <CloseIcon />
              </Button>
            </div>
          </>
        )}
      </div>

      {confirming && (
        <div className="flex items-center gap-1 px-2 pb-2 text-[0.7rem] text-zinc-400">
          <span className="min-w-0 flex-1">
            Delete this thread and every message in it?
          </span>
          <Button
            variant="danger"
            size="xs"
            busy={busy}
            onClick={() => void run(() => onDelete(id).then(() => setConfirming(false)))}
          >
            Delete
          </Button>
          <Button variant="outline" size="xs" onClick={() => setConfirming(false)}>
            Cancel
          </Button>
        </div>
      )}

      {sharing && !renaming && (
        <div className="space-y-1.5 px-2 pb-2 text-[0.7rem] leading-relaxed text-zinc-400">
          <p>
            Anyone with this link can read this thread&apos;s answers. The documents, their filenames and
            the passages they came from stay with you.
          </p>
          {link && (
            <div className="flex items-center gap-1">
              {/* Selectable on purpose: a refused clipboard — insecure context, a browser that says no —
                  leaves the address readable and copyable by hand instead of leaving the feature broken. */}
              <input
                readOnly
                value={link}
                onFocus={(event) => event.target.select()}
                onClick={(event) => event.currentTarget.select()}
                aria-label={`Public link for ${conversation.title}`}
                className={`min-w-0 flex-1 rounded-md border border-zinc-800 bg-zinc-950 px-2 py-1 text-zinc-300 ${FOCUS}`}
              />
              <Button size="xs" onClick={() => void copyLink()}>
                {copied ? "Copied" : "Copy"}
              </Button>
              <Button
                variant="danger"
                size="xs"
                busy={busy}
                onClick={() => void run(() => onUnshare(id).then(stopSharing))}
              >
                Revoke
              </Button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}
