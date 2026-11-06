import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

/**
 * vitest.setup.ts — The browser APIs jsdom does not implement that these components assume.
 *
 * `scrollIntoView` is missing from jsdom entirely, and a citation jump that calls it would throw for a
 * reason that has nothing to do with the component under test. A real browser always has it, so a no-op is
 * the faithful stand-in; the click test still asserts on what the component *did* around that call.
 */
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("scrollTo", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
