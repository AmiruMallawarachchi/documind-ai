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
