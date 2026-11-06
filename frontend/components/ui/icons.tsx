"use client";

/**
 * ui/icons.tsx — The three glyphs in the app, and the only SVGs it draws other than the spinner.
 *
 * Strokes on `currentColor`, so the control that owns an icon colours it with its own text colour and the
 * hover state needs no second rule. An icon library would be a dependency for three paths, and `aria-hidden`
 * on all of them because the control beside the glyph already names it — a button labelled "Delete
 * loan.pdf" does not also need to be read out as "cross".
 */

type Props = {
  className?: string;
};

export function EyeIcon({ className = "h-4 w-4" }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

export function EyeOffIcon({ className = "h-4 w-4" }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M2 12s3.6-7 10-7c2 0 3.8.6 5.3 1.5" />
      <path d="M18.6 6.9C20.7 8.5 22 12 22 12s-3.6 7-10 7c-2 0-3.8-.6-5.3-1.5" />
      <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
      <path d="m3 3 18 18" />
    </svg>
  );
}

/** Two strokes, and it needs `size="xs"` on the delete affordance so the row stays one line tall. */
export function CloseIcon({ className = "h-3.5 w-3.5" }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="m6 6 12 12" />
      <path d="m18 6-12 12" />
    </svg>
  );
}
