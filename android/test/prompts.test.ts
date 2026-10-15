import { describe, expect, it } from "vitest";
import fx from "./fixtures/prompts.json";
import * as P from "../src/core/prompts";
import { BUILTIN_MEETING_TYPES, sanitizeMeetingTypes } from "../src/core/meetingTypes";

describe("prompt parity with desktop prompts.py", () => {
  it("generation prompts (incl. $&, $1, {idx} and the token inside the transcript)", () => {
    for (const c of fx.generation) expect(P.buildGenerationPrompt(c.template, c.style, c.transcript)).toBe(c.output);
  });
  it("reduce, merge and chunk prompts", () => {
    expect(P.buildReducePrompt("T [TRANSCRIPT_HERE]", "Short Summary", "notes $&")).toBe(fx.reduce);
    expect(P.buildMergeNotesPrompt("n {first}", 2, 5, 9)).toBe(fx.merge);
    expect(P.buildChunkPrompt("part $1", 3, 7)).toBe(fx.chunk);
  });
});

describe("prompt library", () => {
  it("has the 25 built-ins in 6 categories", () => {
    expect(P.BUILTIN_PROMPTS).toHaveLength(25);
    expect(P.PROMPT_CATEGORIES).toHaveLength(6);
    expect(P.builtinText("Formal Minutes")).toContain(P.TRANSCRIPT_TOKEN);
  });
  it("names stay unique", () => {
    expect(P.uniqueName("Notes", ["notes", "Notes (2)"])).toBe("Notes (3)");
    expect(P.copyName("Notes", ["Notes (copy)"])).toBe("Notes (copy 2)");
  });
  it("imports desktop exports without overwriting anything", () => {
    const raw = JSON.stringify({ mico360_prompts: 1, prompts: [
      { name: "Mine", category: "Summary", favorite: true, text: "A\r\nB" },
      { name: "Mine", category: "Summary", text: "Other" },
      { name: "Empty", text: "  " }, 5,
    ] });
    const { records, invalid } = P.parsePromptFile("export.json", raw);
    const plan = P.planPromptImport(
      [{ id: "x", name: "Mine", text: "A\nB", builtin: false, category: "Summary", favorite: false }], records);
    expect(invalid).toBe(0);
    expect(plan.skipped).toEqual(["Mine"]);
    expect(plan.toAdd.map((p) => p.name)).toEqual(["Mine (2)"]);
    expect(plan.invalid).toBe(2);
    const back = JSON.parse(P.buildPromptExport(plan.toAdd));
    expect(back.mico360_prompts).toBe(1);
  });
  it("reads a .txt prompt named after the file", () => {
    const { records } = P.parsePromptFile("Weekly_sync.txt", "Write notes [TRANSCRIPT_HERE]");
    expect((records[0] as { name: string }).name).toBe("Weekly sync");
  });
  it("refuses other file types", () => {
    expect(() => P.parsePromptFile("x.docx", "")).toThrow(/Unsupported file type/);
  });
});

describe("meeting types", () => {
  it("ships the six desktop presets and survives bad data", () => {
    expect(BUILTIN_MEETING_TYPES).toHaveLength(6);
    expect(sanitizeMeetingTypes([{ name: "A", style: 3 }, { name: "" }, "x", null])).toEqual([
      { name: "A", prompt_name: "", style: "3", profile_name: "" },
    ]);
  });
});
