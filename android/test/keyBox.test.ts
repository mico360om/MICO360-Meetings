import { describe, expect, it } from "vitest";
import { hideKey, revealKey } from "../src/core/keyBox";

describe("key box", () => {
  it("round-trips a key and never stores it in clear", () => {
    const box = hideKey("mico_" + "ab".repeat(24));
    expect(revealKey(box)).toBe("mico_" + "ab".repeat(24));
    expect(JSON.stringify(box)).not.toContain("mico_");
    expect(box.s).toMatch(/^[0-9a-f]{64}$/);
  });
  it("a different salt gives different data", () => {
    expect(hideKey("k", new Uint8Array(32).fill(1)).d).not.toBe(hideKey("k", new Uint8Array(32).fill(2)).d);
  });
  it("missing or broken boxes reveal nothing", () => {
    expect(revealKey(null)).toBe("");
    expect(revealKey({ s: "zz", d: "00" })).toBe("");
  });
});
