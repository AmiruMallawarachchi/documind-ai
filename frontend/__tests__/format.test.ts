import { describe, expect, it } from "vitest";

import { formatBytes } from "@/lib/format";

/**
 * __tests__/format.test.ts — The byte-size display contract.
 *
 * Includes the one output that looks like a bug and is not: a file one byte short of a megabyte reads
 * "1024 KB", because the display rounds within the unit it has actually reached.
 */

describe("formatBytes", () => {
  it("names the unit each size has reached", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1023)).toBe("1023 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(1024 * 1024)).toBe("1.0 MB");
    expect(formatBytes(3 * 1024 * 1024 + 512 * 1024)).toBe("3.5 MB");
  });

  it("rounds to the precision the unit can afford", () => {
    // 15360 / 1024 = 15 exactly; 16907 / 1024 = 16.51… and kilobytes are shown whole.
    expect(formatBytes(15 * 1024)).toBe("15 KB");
    expect(formatBytes(16907)).toBe("17 KB");
  });

  it("does not promote a size that has not reached the next unit", () => {
    expect(formatBytes(1024 * 1024 - 1)).toBe("1024 KB");
  });
});
