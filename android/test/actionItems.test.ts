import { describe, expect, it } from "vitest";
import fx from "./fixtures/actionItems.json";
import * as A from "../src/core/actionItems";

const toPy = (it: Pick<A.ActionItem, "task" | "owner" | "deadline" | "status" | "priority">) =>
  ({ task: it.task, owner: it.owner, deadline: it.deadline, status: it.status, priority: it.priority });
const BOM = String.fromCharCode(0xfeff);

describe("parity with desktop tasks.py", () => {
  it("deadlines", () => { for (const c of fx.deadlines) expect([c.input, A.parseDeadline(c.input, c.ref)]).toEqual([c.input, c.output]); });
  it("status", () => { for (const c of fx.status) expect(A.normalizeStatus(c.input)).toBe(c.output); });
  it("priority", () => { for (const c of fx.priority) expect(A.normalizePriority(c.input)).toBe(c.output); });
  it("extract", () => {
    for (const c of fx.extract) expect(A.extractActionItems(c.input).map(toPy)).toEqual(c.items.map(toPy));
  });
  it("csvSafe", () => { for (const c of fx.csvSafe) expect(A.csvSafe(c.input)).toBe(c.output); });
});

describe("tracking", () => {
  const md = "| Task | Owner | Deadline | Status |\n|---|---|---|---|\n| Send report | Ali | 2026-10-01 | Pending |\n| Send report | Bob | 2026-10-30 | Done |";
  const items = A.itemsForMeeting({ id: "m1", title: "Budget", createdAt: Date.UTC(2026, 9, 5, 12), minutes: md });
  it("same task text in one meeting gets distinct keys", () => {
    const h = A.taskHash("Send report");
    expect(items.map((i) => i.okey)).toEqual([`m1:${h}:0`, `m1:${h}:1`]);
  });
  it("overdue is derived and closed tasks are never overdue", () => {
    expect(A.effectiveStatus(items[0], "2026-10-15")).toBe("Overdue");
    expect(A.effectiveStatus(items[1], "2026-10-15")).toBe("Completed");
    expect(A.reminderCounts(items, "2026-09-28")).toEqual({ overdue: 0, dueSoon: 1 });
  });
  it("overrides replace edited fields; a legacy string is a status", () => {
    const it2 = A.applyOverride({ ...items[0] }, { owner: "Sara", status: "In Progress" });
    expect([it2.owner, it2.status]).toEqual(["Sara", "In Progress"]);
    expect(A.applyOverride({ ...items[0] }, "Completed").status).toBe("Completed");
  });
  it("CSV for Excel: BOM, CRLF, quoting, formula neutralised", () => {
    const csv = A.buildActionItemsCsv([{ ...items[0], task: '=HYPERLINK("x"), "q"' }]);
    expect(csv.startsWith(BOM + "Task,Responsible,Deadline,Priority,Status,Notes,Meeting,Meeting date\r\n")).toBe(true);
    expect(csv).toContain(`"'=HYPERLINK(""x""), ""q"""`);
  });
});
