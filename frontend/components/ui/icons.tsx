"use client";

/**
 * ui/icons.tsx — The glyphs in the app, and the only SVGs it draws other than the spinner.
 *
 * Strokes on `currentColor`, so the control that owns an icon colours it with its own text colour and the
 * hover state needs no second rule. An icon library would be a dependency for six paths, and `aria-hidden`
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

/**
 * The history row's two quiet actions. Drawn at `h-3.5` like `CloseIcon`, because three glyphs in a
 * 20rem sidebar have to share one line with a title that already truncates.
 */
export function PinIcon({ className = "h-3.5 w-3.5" }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M9 3h6l-1.5 6L17 12H7l3.5-3L9 3Z" />
      <path d="M12 12v9" />
    </svg>
  );
}

/** A pencil, not a pen: this renames a thread, and the row that owns it is the one that says so. */
export function PencilIcon({ className = "h-3.5 w-3.5" }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M4 20h4L20 8l-4-4L4 16v4Z" />
      <path d="m14 6 4 4" />
    </svg>
  );
}

/**
 * Two hooks. The row's fourth glyph, and the one whose button has to say more than the picture: a link
 * means *anyone with this can read it*, which is not guessable from a chain.
 */
export function LinkIcon({ className = "h-3.5 w-3.5" }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1" />
      <path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7.1 7.1l1.1-1.1" />
    </svg>
  );
}
