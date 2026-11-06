"use client";

/**
 * ui/Banner.tsx — A sentence from the server that the reader did not ask for.
 *
 * Four of the six banners in this app are the same red box, and the two that are not are the same box in
 * a different colour. The colour is the only thing that varies, so it is the only thing worth naming.
 *
 * A `<div>` rather than a `<p>`, because the amber one carries a button — and a block-level control
 * inside a paragraph is invalid HTML that no test would ever complain about, it simply stops being a
 * paragraph when the parser meets the button.
 *
 * `role="alert"` is left to the caller on purpose. A rejection the visitor caused is worth interrupting
 * a screen reader for; a notice confirming something they just did is not, and announcing both makes the
 * important one ordinary.
 */

import type { HTMLAttributes, ReactNode } from "react";

const TONE = {
  error: "bg-red-950/50 text-red-300",
  info: "bg-indigo-950/50 text-indigo-200",
  warning: "bg-amber-950/40 text-amber-200",
} as const;

type Props = HTMLAttributes<HTMLDivElement> & {
  tone?: keyof typeof TONE;
  children: ReactNode;
};

export default function Banner({ tone = "error", className = "", children, ...rest }: Props) {
  return (
    <div
      className={`rounded-lg px-3 py-2 text-xs leading-relaxed ${TONE[tone]} ${className}`}
      {...rest}
    >
      {children}
    </div>
  );
}
