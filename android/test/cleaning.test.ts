import { describe, expect, it } from "vitest";
import fx from "./fixtures/cleaning.json";
import { chunkText, cleanTranscript, splitlines } from "../src/core/cleaning";

describe("cleaning parity with desktop cleaning.py", () => {
  it.each(fx.clean.map((c, i) => [i, c] as const))("clean case %i", (_i, c) => {
    expect(cleanTranscript(c.input, c.fillers)).toBe(c.output);
  });
  it.each(fx.chunk.map((c, i) => [i, c] as const))("chunk case %i", (_i, c) => {
    expect(chunkText(c.input, c.max, c.overlap)).toEqual(c.chunks);
  });
});

describe("splitlines like Python", () => {
  it("drops one trailing empty line and splits on every line break", () => {
    expect(splitlines("a\nb\n")).toEqual(["a", "b"]);
    expect(splitlines("a\r\nb\rc d")).toEqual(["a", "b", "c", "d"]);
    expect(splitlines("")).toEqual([]);
  });
});
