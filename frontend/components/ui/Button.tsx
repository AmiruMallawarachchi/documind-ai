"use client";

/**
 * ui/Button.tsx — Every clickable control that is not a link, in one place.
 *
 * Twenty buttons across five panels were written as twenty hand-typed class strings, and they had
 * already drifted: the upload button kept its indigo when disabled while the sign-in button greyed out,
 * the two "Sign out" controls were the same action styled two ways (a bordered outline in the header, a
 * bare text link in the footer, with a different border colour each), and the spinner existed in three
 * copies — two identical SVGs and one CSS-border span. Centralising them is also what makes the two
 * rules below impossible to forget on the twenty-first button.
 *
 * `busy` is deliberately not `disabled` styling. A control mid-request must keep the colour it had when
 * it was pressed: grey on a button someone just clicked reads as "this broke", when the truth is only
 * "the server has not answered yet". So `busy` freezes the idle classes, holds the spinner slot, and
 * still sets `aria-busy` for anyone using a screen reader.
 *
 * The weight lives with the variant, not the size, because these are not typography choices — a
 * `primary` is an action and a `ghost` is a correction, and the weight is how you tell them apart at a
 * glance.
 */

import type { ButtonHTMLAttributes } from "react";

import Spinner from "./Spinner";

const STYLE = {
  primary: {
    idle: "hover:bg-indigo-500 disabled:bg-zinc-800 disabled:text-zinc-600",
    rest: "rounded-xl bg-indigo-600 font-semibold text-white",
  },
  outline: {
    idle: "hover:border-zinc-600 hover:text-zinc-100",
    rest: "rounded-lg border border-zinc-700 font-medium text-zinc-300",
  },
  /** Same rectangle, and the hover says "this one ends something". Only the stop control uses it, and
   *  it exists because `className` cannot override a hover: two `hover:` utilities in one class list
   *  resolve by the order Tailwind emitted them, not by the order they were typed. */
  outlineDanger: {
    idle: "hover:border-red-800 hover:text-red-300",
    rest: "rounded-xl border border-zinc-700 font-medium text-zinc-200",
  },
  ghost: {
    idle: "hover:text-zinc-200",
    rest: "font-normal text-zinc-400",
  },
  /** A bare glyph in the corner of a row. Its hover colour is the action it wraps, and today that is
   *  always delete. */
  icon: {
    idle: "hover:bg-zinc-800 hover:text-red-400",
    rest: "rounded-lg font-normal text-zinc-600",
  },
  danger: {
    idle: "hover:bg-red-600",
    rest: "rounded-lg bg-red-600/80 font-medium text-white",
  },
  warning: {
    idle: "hover:bg-amber-500/25 disabled:opacity-60",
    rest: "rounded-lg bg-amber-500/15 font-semibold text-amber-200",
  },
} as const;

const SIZE = {
  md: "px-4 py-2.5 text-sm",
  sm: "px-3 py-1.5 text-xs",
  xs: "px-2.5 py-1 text-[0.7rem]",
} as const;

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof STYLE;
  size?: keyof typeof SIZE;
  /** Stretches to the width of its container, which is what a panel's main action does. */
  block?: boolean;
  busy?: boolean;
};

export default function Button({
  variant = "primary",
  size = "md",
  block = false,
  busy = false,
  className = "",
  children,
  ...rest
}: Props) {
  const style = STYLE[variant];
  const classes = [
    "inline-flex items-center justify-center gap-2 transition-colors disabled:cursor-not-allowed",
    SIZE[size],
    style.rest,
    busy ? "cursor-not-allowed" : style.idle,
    block && "w-full",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button type="button" aria-busy={busy || undefined} className={classes} {...rest}>
      {busy && <Spinner />}
      {children}
    </button>
  );
}
