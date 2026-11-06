"use client";

/**
 * ui/Spinner.tsx — The one loading glyph the app uses.
 *
 * It was three copies of two designs — the same four-element SVG in the sign-in button and the upload
 * button, and a bordered `span` doing the same job in the chat status line — which is the kind of
 * duplication that ends with three spinners spinning at different speeds. The `span` version is gone: an
 * SVG scales with `font-size`-free sizing props and looks the same inside a button and inside a sentence.
 *
 * `aria-hidden` is not decoration here: a spinner is a picture of a state the control already announces
 * through `aria-busy`, and reading it out as well would make a screen reader say nothing twice.
 */

type Props = {
  className?: string;
};

export default function Spinner({ className = "h-4 w-4" }: Props) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z" />
    </svg>
  );
}
