import { describe, expect, it } from "vitest";
import fx from "./fixtures/mdBlocks.json";
import rtlFx from "./fixtures/rtl.json";
import { cleanText, parse, plain, runs } from "../src/core/mdBlocks";
import { hasArabic } from "../src/core/rtl";

describe("mdBlocks parity with desktop md_blocks.py", () => {
  it.each(fx.parse.map((c, i) => [i, c] as const))("parse case %i", (_i, c) => {
    expect(parse(c.input)).toEqual(c.blocks);
  });
  it("runs", () => { for (const c of fx.runs) expect(runs(c.input)).toEqual(c.runs); });
  it("cleanText", () => { for (const c of fx.clean) expect(cleanText(c.input)).toEqual(c.output); });
  it("rtl", () => { for (const c of rtlFx) expect(hasArabic(c.input)).toBe(c.arabic); });
});

describe("edge cases", () => {
  it("keeps emoji and drops only lone surrogates", () => {
    expect(cleanText("a😀b\ud800c\udc00d")).toBe("a😀bcd");
  });
  it("plain strips bold markers", () => expect(plain("**Key:** value")).toBe("Key: value"));
  it("never hangs on a stray pipe line", () => {
    expect(parse("|\n|\n|").length).toBeGreaterThan(0);
  });
});
