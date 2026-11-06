import { describe, expect, it } from "vitest";

import { traceLabel } from "@/lib/trace";

/**
 * __tests__/trace.test.ts — What the debug trail says about each step the graph took.
 *
 * The interesting case is the third one: the node names are the graph's own, so a rename on the backend
 * must not blank a step out of the reader's view.
 */

describe("traceLabel", () => {
  it("translates a known node", () => {
    expect(traceLabel("retrieval")).toBe("searched");
    expect(traceLabel("finalise")).toBe("answered");
  });

  it("keeps the detail the node reported with it", () => {
    expect(traceLabel("retrieval:6")).toBe("searched 6");
    expect(traceLabel("critique:reject")).toBe("verified reject");
  });

  it("passes an unknown node through rather than dropping it", () => {
    expect(traceLabel("reranker:3")).toBe("reranker 3");
  });
});
