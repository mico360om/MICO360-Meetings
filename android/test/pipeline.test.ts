import { beforeEach, describe, expect, it } from "vitest";
import { CancelledError, clearPartCache, groupNotes, runMinutesPipeline } from "../src/core/pipeline";

const longText = Array.from({ length: 400 }, (_, i) => `Point ${i} about the plan.`).join(" ");

describe("minutes pipeline", () => {
  beforeEach(() => clearPartCache());

  it("short transcripts are one request with the template and style", async () => {
    const sent: string[] = [];
    const out = await runMinutesPipeline(async (p) => { sent.push(p); return "MINUTES"; },
      { model: "m", transcript: "Hello team.", template: "T [TRANSCRIPT_HERE]", style: "Short Summary" });
    expect(out).toBe("MINUTES");
    expect(sent).toHaveLength(1);
    expect(sent[0]).toContain("T Hello team.");
    expect(sent[0]).toContain("Style instruction:");
  });

  it("long transcripts: one request per part, then the reduce with the same template", async () => {
    const sent: string[] = [];
    await runMinutesPipeline(async (p) => { sent.push(p); return "notes"; },
      { model: "m", transcript: longText, template: "TEMPLATE [TRANSCRIPT_HERE]", chunkChars: 2000 });
    const parts = sent.filter((p) => p.includes("Transcript part"));
    expect(parts.length).toBeGreaterThan(2);
    expect(sent[sent.length - 1]).toMatch(/^TEMPLATE \[This meeting was long/);
  });

  it("a retry reuses finished parts (only the failed part is asked again)", async () => {
    let calls = 0;
    const flaky = async (p: string) => {
      calls += 1;
      if (p.includes("Transcript part 2")) throw new Error("boom");
      return "notes";
    };
    await expect(runMinutesPipeline(flaky, { model: "m", transcript: longText, template: "T", chunkChars: 2000 })).rejects.toThrow("boom");
    expect(calls).toBe(2);
    const sent: string[] = [];
    await runMinutesPipeline(async (p) => { sent.push(p); return "notes"; },
      { model: "m", transcript: longText, template: "T", chunkChars: 2000 });
    expect(sent.some((p) => p.includes("Transcript part 1 of"))).toBe(false);
    expect(sent.some((p) => p.includes("Transcript part 2 of"))).toBe(true);
  });

  it("notes too long for one reduce are merged in groups first", async () => {
    const sent: string[] = [];
    await runMinutesPipeline(async (p) => { sent.push(p); return "n".repeat(900); },
      { model: "m", transcript: longText, template: "T", chunkChars: 1500, reduceBudget: 2500 });
    expect(sent.some((p) => p.startsWith("You are condensing notes"))).toBe(true);
  });

  it("cancel stops between requests", async () => {
    const ctl = new AbortController();
    const run = runMinutesPipeline(async () => { ctl.abort(); return "notes"; },
      { model: "m", transcript: longText, template: "T", chunkChars: 2000, signal: ctl.signal });
    await expect(run).rejects.toBeInstanceOf(CancelledError);
  });

  it("empty transcripts and missing models are refused", async () => {
    await expect(runMinutesPipeline(async () => "", { model: "", transcript: "x", template: "T" })).rejects.toThrow("No AI model");
    await expect(runMinutesPipeline(async () => "", { model: "m", transcript: "  um  ", template: "T" })).rejects.toThrow("empty");
  });

  it("groupNotes pairs neighbours when nothing fits", () => {
    expect(groupNotes(["aaaa", "bbbb", "cccc"], 3)).toEqual([["aaaa", "bbbb"], ["cccc"]]);
    expect(groupNotes(["a", "b", "c"], 100)).toEqual([["a", "b", "c"]]);
  });
});
