import { expect, it } from "vitest";
import { insertBlock, prefixLines, TABLE_TEMPLATE, wrap } from "../src/ui/editing";

it("bold wraps the selection, or a placeholder when nothing is selected", () => {
  expect(wrap("make it so", { start: 5, end: 7 }, "**", "bold")).toEqual({ text: "make **it** so", sel: { start: 7, end: 9 } });
  expect(wrap("ab", { start: 1, end: 1 }, "*", "italic").text).toBe("a*italic*b");
});

it("headings and bullets apply to whole lines, once, replacing the other kind", () => {
  expect(prefixLines("one\ntwo", { start: 5, end: 6 }, "## ").text).toBe("one\n## two");
  expect(prefixLines("- a\n- b", { start: 0, end: 7 }, "- ").text).toBe("- a\n- b");
  expect(prefixLines("## a", { start: 0, end: 4 }, "- ").text).toBe("- a");
});

it("a table starts on its own paragraph", () => {
  const r = insertBlock("Notes", { start: 5, end: 5 }, TABLE_TEMPLATE);
  expect(r.text).toBe("Notes\n\n" + TABLE_TEMPLATE);
  expect(r.sel.start).toBe(r.text.length);
  expect(insertBlock("", { start: 0, end: 0 }, "x").text).toBe("x");
});
