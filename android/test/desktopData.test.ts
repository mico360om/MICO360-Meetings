import { describe, expect, it } from "vitest";
import data from "../src/core/data/desktop.json";
import { LETTER, WORD } from "../src/core/unicode";

describe("desktop data", () => {
  it("has the desktop's shared text", () => {
    expect(data.transcriptToken).toBe("[TRANSCRIPT_HERE]");
    expect(data.builtinPrompts.length).toBe(25);
    expect(data.outputStyles.length).toBe(5);
    expect(data.meetingTypes.length).toBe(6);
    expect(data.pdfDesigns.map((d) => d.key)).toEqual(["classic", "modern", "formal", "compact"]);
    expect(data.connect.baseUrl).toBe("http://ai.mico360.com:5310/v1");
    expect(data.connect.httpsCandidates[0]).toBe("https://ai.mico360.com:5311/v1");
  });
});

describe("unicode classes", () => {
  const word = new RegExp(`^[${WORD}]+$`, "u");
  const letter = new RegExp(`^[${LETTER}]+$`, "u");
  it("match letters in any script and digits like Python's \\w", () => {
    for (const s of ["abc", "Ünïcödé", "مرحبا", "שלום", "日本語", "x_1", "٣٤"]) expect(word.test(s)).toBe(true);
    for (const s of [" ", "-", "'", ".", "،", "😀"]) expect(word.test(s)).toBe(false);
  });
  it("letters exclude decimal digits and underscore", () => {
    expect(letter.test("اجتماع")).toBe(true);
    expect(letter.test("a1")).toBe(false);
    expect(letter.test("a_b")).toBe(false);
  });
});
