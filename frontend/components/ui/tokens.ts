/**
 * components/ui/tokens.ts — The class strings the panels each kept a private copy of.
 *
 * Tailwind utilities are the vocabulary; this file is only the spelling. A panel that needs a form field
 * should not retype its border, radius and placeholder colour, because the fourth copy is always a
 * little different from the first three and nothing says which one was the house style. The six alert
 * banners that came before `ui/Banner` were one shape typed six ways: five used `py-2` and one `py-2.5`,
 * only the indigo one carried `leading-relaxed`, and the element alternated between `<p>` and `<div>` on
 * whether the banner holds a button — which is why `ui/Banner` renders a `<div>` always.
 *
 * Deliberately small: a string used by one component stays in that component.
 */

/** The box a value is typed into. Shared by `input` and `textarea`, so they cannot drift apart. */
export const FIELD =
  "w-full rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60";

/** The small-caps label above a field. */
export const FIELD_LABEL =
  "block text-[0.7rem] font-semibold uppercase tracking-widest text-zinc-500";

/**
 * The quiet explanatory line under a control. `leading-relaxed` because every one of them wraps to two
 * lines at some viewport width, and the tight leading that suits a single line makes a wrapped pair read
 * as one word run on.
 */
export const HINT = "text-[0.7rem] leading-relaxed text-zinc-600";

/** Sidebar section heading. */
export const SECTION_TITLE =
  "mb-2 text-[0.7rem] font-semibold uppercase tracking-widest text-zinc-500";
