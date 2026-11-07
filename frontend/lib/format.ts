/**
 * lib/format.ts — Turning a number the server sent into something worth reading.
 *
 * Byte sizes are the one place this app rounds a measurement for display, so the rounding rule is stated
 * here rather than inferred from the output: a size is shown in the largest unit where it is still at
 * least one whole of that unit, rounded to whatever precision that unit can afford. `1048575` bytes is
 * therefore "1024 KB" and not "1.0 MB" — the file really is one byte short of a megabyte, and a display
 * that rounded it up would be claiming a unit it had not reached.
 */

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * A date a reader sees, not a timestamp a machine parses.
 *
 * The shared page is the only place in the app that shows one, and it earns its place: an answer about a
 * document is a statement about the file *as of* that day, and a recipient who is sent the link a month
 * later should be able to tell.
 *
 * The locale is explicit rather than `undefined` because the output is asserted in a test and rendered for
 * a reader: inheriting the visitor's locale would make the same link say different things in different
 * browsers, and make the test pass or fail on the machine that runs it.
 */
export function formatDate(stamp: string): string {
  return new Date(stamp).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}
