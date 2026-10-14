import { describe, expect, it } from "vitest";
import fx from "./fixtures/ics.json";
import type { ActionItem } from "../src/core/actionItems";
import { buildIcs, eventUid, foldLine } from "../src/core/ics";

const items: ActionItem[] = fx.items.map((i) => ({
  task: String(i.task), owner: String(i.owner), deadline: String(i.deadline), status: String(i.status),
  priority: String(i.priority), notes: String(i.notes), meetingId: String(i.meeting_id), meetingTitle: String(i.meeting_title),
  meetingDate: String(i.meeting_date), okey: String(i.okey), occ: Number(i.occ),
}));

describe("ics parity with desktop calendar_ics.py", () => {
  it("same calendar, line for line (DTSTAMP aside)", () => {
    const out = buildIcs(items, { now: new Date(Date.UTC(2026, 9, 15, 8, 30)) });
    const lines = out.split("\r\n").filter((l) => !l.startsWith("DTSTAMP:")).join("\r\n");
    expect(lines).toBe(fx.ics);
    expect(out).toContain("DTSTAMP:20261015T083000Z");
  });
  it("same stable UIDs", () => { expect(items.map(eventUid)).toEqual(fx.uids); });
  it("folds by UTF-8 octets, never splitting a character", () => {
    const folded = foldLine("SUMMARY:" + "ع".repeat(80));
    for (const part of folded.split("\r\n")) expect(new TextEncoder().encode(part).length).toBeLessThanOrEqual(75);
    expect(folded.split("\r\n").map((p, i) => (i ? p.slice(1) : p)).join("")).toBe("SUMMARY:" + "ع".repeat(80));
  });
});
