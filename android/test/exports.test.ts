import { describe, expect, it } from "vitest";
import { safeFilename } from "../src/core/fileNames";
import { type ExportDeps, exportActionItems, exportMinutes } from "../src/services/exports";
import { DEFAULT_PROFILE } from "../src/core/profiles";
import type { ActionItem } from "../src/core/actionItems";
import type { Meeting } from "../src/data/meetings";

const meeting = { id: "m1", title: "Q4 / Budget: review?", minutes: "# Meeting Minutes\n**Meeting Title:** Budget\n## Notes\n- one" } as Meeting;

function fakeDeps() {
  const writes: Array<[string, string]> = [];
  const deps: ExportDeps = {
    pdfFromHtml: async (html) => { writes.push(["pdf-html", html]); return "file:///cache/print.pdf"; },
    writeText: async (name, text) => { writes.push([name, text]); return "file:///exports/" + name; },
    writeBase64: async (name, b64) => { writes.push([name, b64]); return "file:///exports/" + name; },
    moveTo: async (_uri, name) => "file:///exports/" + name,
    logo: async () => null,
  };
  return { deps, writes };
}

describe("file names", () => {
  it("are safe on every platform", () => {
    expect(safeFilename("Q4 / Budget: review?")).toBe("Q4 Budget review");
    expect(safeFilename("")).toBe("Meeting-Minutes");
    expect(safeFilename("CON")).toBe("_CON");
    expect(safeFilename("con.txt")).toBe("_con.txt");
    expect(safeFilename("محضر الاجتماع")).toBe("محضر الاجتماع");
    expect(safeFilename("a".repeat(150)).length).toBe(100);
    expect(safeFilename("trailing dots... ")).toBe("trailing dots");
    expect(safeFilename(" ... ")).toBe("Meeting-Minutes");
  });
});

describe("minutes exports", () => {
  it("PDF goes through the branded HTML in the company's design and is named after the meeting", async () => {
    const { deps, writes } = fakeDeps();
    const r = await exportMinutes(meeting, { ...DEFAULT_PROFILE, id: "p", name: "Acme", pdf_design: "formal" }, "pdf", deps);
    expect(r).toEqual({ uri: "file:///exports/Q4 Budget review.pdf", mime: "application/pdf", name: "Q4 Budget review.pdf" });
    expect(writes[0][1]).toContain("design-formal");
  });
  it("Word, text and Markdown", async () => {
    const { deps, writes } = fakeDeps();
    expect((await exportMinutes(meeting, null, "docx", deps)).mime).toBe("application/vnd.openxmlformats-officedocument.wordprocessingml.document");
    expect((await exportMinutes(meeting, null, "txt", deps)).name).toBe("Q4 Budget review.txt");
    expect((await exportMinutes(meeting, null, "md", deps)).mime).toBe("text/markdown");
    expect(writes.find(([n]) => n.endsWith(".txt"))![1]).not.toContain("**");
  });
  it("empty minutes are refused with a clear message", async () => {
    await expect(exportMinutes({ ...meeting, minutes: "  " }, null, "pdf", fakeDeps().deps)).rejects.toThrow(/no minutes/i);
  });
});

describe("action item exports", () => {
  const item: ActionItem = { task: "Send", owner: "Ali", deadline: "2026-10-20", status: "Pending", priority: "", notes: "",
    meetingId: "m1", meetingTitle: "Budget", meetingDate: "2026-10-05", okey: "m1:abc:0", occ: 0 };
  it("CSV and calendar", async () => {
    const { deps } = fakeDeps();
    expect(await exportActionItems([item], "csv", deps)).toMatchObject({ name: "Action-Items.csv", mime: "text/csv", count: 1 });
    expect(await exportActionItems([item], "ics", deps)).toMatchObject({ name: "Action-Items.ics", mime: "text/calendar", count: 1 });
  });
  it("a calendar with no usable dates is refused", async () => {
    await expect(exportActionItems([{ ...item, deadline: "Not specified" }], "ics", fakeDeps().deps)).rejects.toThrow(/date/i);
  });
});
