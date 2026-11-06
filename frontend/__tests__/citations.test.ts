import { describe, expect, it } from "vitest";

import { parseAnswer } from "@/lib/citations";

/**
 * __tests__/citations.test.ts — The marker parser on its own, with no component in the way.
 *
 * Message's tests cover what the reader sees; these cover the contract the renderer is built on, including
 * the two shapes a renderer cannot show: an answer that is nothing but markers, and the empty pieces a
 * split produces at a boundary.
 */

const text = (content: string) =>
  parseAnswer(content).flatMap((part) => (part.kind === "text" ? [part.text] : []));

const markers = (content: string) =>
  parseAnswer(content).flatMap((part) => (part.kind === "markers" ? part.indexes : []));

describe("parseAnswer", () => {
  it("keeps prose without markers as one piece", () => {
    expect(parseAnswer("Nothing cited here.")).toEqual([
      { kind: "text", text: "Nothing cited here." },
    ]);
  });

  it("splits on a single marker, digits as a number", () => {
    expect(parseAnswer("Fee is 250 dollars [1].")).toEqual([
      { kind: "text", text: "Fee is 250 dollars " },
      { kind: "markers", indexes: [1] },
      { kind: "text", text: "." },
    ]);
  });

  it("reads a chained run as one piece holding every index", () => {
    expect(markers("Both clauses apply [1][3][12].")).toEqual([1, 3, 12]);
  });

  it("drops the empty strings the split leaves at its own boundaries", () => {
    expect(parseAnswer("[1]")).toEqual([{ kind: "markers", indexes: [1] }]);
  });

  it("leaves brackets that are not markers in the text", () => {
    expect(markers('The "[signed copy only]" clause.'))
      .toEqual([]);
    expect(text('The "[signed copy only]" clause.')).toEqual([
      'The "[signed copy only]" clause.',
    ]);
  });

  it("treats a bare marker run as an answer with no prose at all", () => {
    // Not something the model says today, and worth knowing the renderer does not crash on.
    expect(parseAnswer("[2]")).toEqual([{ kind: "markers", indexes: [2] }]);
  });

  it("handles an empty answer without inventing a piece", () => {
    expect(parseAnswer("")).toEqual([]);
  });
});
