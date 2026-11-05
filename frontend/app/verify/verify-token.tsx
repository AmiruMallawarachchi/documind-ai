"use client";

/**
 * app/verify/verify-token.tsx — Spends the confirmation link that is in the URL.
 *
 * The whole page is one POST with the token from the query string in it. No email is sent alongside: the
 * token carries the account id, so a link cannot be pointed at an address it was not minted for.
 *
 * Two details here are load-bearing rather than decorative.
 *
 * The request is guarded so it runs once. React StrictMode mounts effects twice in development, and
 * re-posting a spent link answers 200 — confirming an already-confirmed address is the idempotent case — so
 * the double invoke would look harmless and still be a wasted round trip whose only visible difference is
 * which sentence the visitor is left with.
 *
 * The token is then dropped from the address bar. The link works once and lasts a day, so leaving it in the
 * URL means a reload, a revisit from browser history, or a screenshot pasted into a chat all carry a live
 * credential for someone else's account.
 */

import { useEffect, useRef, useState } from "react";

import { useSearchParams } from "next/navigation";

import { verifyEmail } from "@/lib/session";
import VerifyNotice from "./verify-notice";

export default function VerifyToken() {
  const searchParams = useSearchParams();
  // A URL can repeat a parameter; the first is the one the mail client opened. An empty value is a link that
  // got truncated in transit, which is an error to name rather than a token to send.
  const token = searchParams.get("token") ?? "";

  const [state, setState] = useState<{ phase: "working" | "done" | "error"; message?: string }>(
    token ? { phase: "working" } : { phase: "error", message: "That link has no confirmation token in it." },
  );
  const started = useRef(false);

  useEffect(() => {
    if (!token || started.current) return;
    started.current = true;

    void verifyEmail(token).then((result) => {
      window.history.replaceState(null, "", "/verify");
      setState(
        "error" in result
          ? { phase: "error", message: result.error }
          : { phase: "done", message: result.message },
      );
    });
  }, [token]);

  return <VerifyNotice {...state} />;
}
