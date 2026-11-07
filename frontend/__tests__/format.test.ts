import { describe, expect, it } from "vitest";

import { formatBytes, formatDate } from "@/lib/format";

/**
 * __tests__/format.test.ts — The two things this app rounds for display: a size and a date.
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

describe("formatDate", () => {
  it("writes the day out for a reader, not for a parser", () => {
    // Stamped at midday UTC on purpose: a date rendered in the visitor's own zone has to be far enough from
    // both edges of the day that the assertion means the same thing in Colombo and in CI.
    expect(formatDate("2026-11-06T12:00:00Z")).toBe("6 November 2026");
    expect(formatDate("2027-01-01T12:00:00Z")).toBe("1 January 2027");
  });

  it("does not depend on the order the month and day arrive in", () => {
    // The backend sends ISO and the browser sends a locale; only the words are asserted, because that is
    // what a reader notices when a shared page says "11/6/2026" and nobody knows which is the month.
    expect(formatDate("2026-11-06T12:00:00Z")).not.toMatch(/\d+\/\d+\/\d+/);
  });
});
