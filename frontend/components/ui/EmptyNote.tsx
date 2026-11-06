"use client";

/**
 * ui/EmptyNote.tsx — What a list says before it has anything to show.
 *
 * A dashed box rather than a line of grey text, because the sidebar is a column of small grey text and a
 * sentence in it is invisible to someone scanning for their file. The box says *nothing here yet* at a
 * glance; the words inside say which kind of nothing.
 *
 * It renders an `<li>` because both lists that use it are `<ul>`s, and a `div` as a list item is markup a
 * validator complains about while every browser renders it anyway.
 */

import type { ReactNode } from "react";

export default function EmptyNote({ children }: { children: ReactNode }) {
  return (
    <li className="rounded-lg border border-dashed border-zinc-800 px-3 py-4 text-center text-xs leading-relaxed text-zinc-500">
      {children}
    </li>
  );
}
