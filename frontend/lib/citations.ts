/**
 * lib/citations.ts — Reading the `[n]` markers back out of an answer.
 *
 * This parser lives here rather than inside the component because it is the part that was wrong:
 * `String.split` hands back exactly what a capture group matched, so a group that stops before the closing
 * `]` makes every returned piece unrecognisable, and the citation renders as readable prose with a stray
 * bracket in it. That is logic with four distinct shapes to cover — single, chained `[1][3]`, two-digit,
 * and brackets that are not markers at all — and none of them need a DOM to prove.
 */

/** A run of adjacent markers, the only piece of a split answer that becomes buttons instead of text. */
const MARKER_RUN = /^(?:\[\d+\])+$/;

export type AnswerPart =
  | { kind: "text"; text: string }
  | { kind: "markers"; indexes: number[] };

export function parseAnswer(content: string): AnswerPart[] {
  // The repeating group has to contain the closing `]`, and the whole piece has to match `MARKER_RUN`, so
  // `[signed copy only]` stays a sentence and `[1][3] becomes two buttons.
  return content
    .split(/((?:\[\d+\])+)/g)
    .filter((part) => part !== "")
    .map((part): AnswerPart => {
      if (!MARKER_RUN.test(part)) return { kind: "text", text: part };
      return {
        kind: "markers",
        indexes: [...part.matchAll(/\d+/g)].map(([digits]) => Number(digits)),
      };
    });
}
