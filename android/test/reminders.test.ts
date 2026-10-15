import { expect, it } from "vitest";
import { MAX_REMINDERS, planReminders } from "../src/core/reminders";
import type { ActionItem } from "../src/core/actionItems";

const item = (task: string, deadline: string, status = "Pending", okey = task): ActionItem => ({ task, owner: "Ali", deadline, status,
  priority: "", notes: "", meetingId: "m", meetingTitle: "Budget", meetingDate: "2026-10-05", okey, occ: 0 });

it("reminds the day before and on the day at 09:00, only for open items in the future", () => {
  const now = new Date(2026, 9, 14, 10, 0);
  const plans = planReminders([item("Send report", "2026-10-16"), item("Done one", "2026-10-16", "Completed"),
    item("Past", "2026-10-01"), item("Tomorrow only", "2026-10-15")], now);
  // 10:00 on the 14th: "Tomorrow only"'s day-before reminder (09:00 on the 14th) is already past.
  expect(plans.map((p) => [p.title, p.at.getDate(), p.at.getHours()])).toEqual([
    ["Due tomorrow: Send report", 15, 9], ["Due today: Tomorrow only", 15, 9], ["Due today: Send report", 16, 9]]);
  expect(plans.every((p) => p.at > now)).toBe(true);
  expect(plans[0].body).toBe("Ali · Budget");
  expect(new Set(plans.map((p) => p.id)).size).toBe(plans.length);
});

it("never schedules more than the limit, nearest first", () => {
  const now = new Date(2026, 0, 1, 8, 0);
  const many = Array.from({ length: 80 }, (_x, i) => item(`T${i}`, `2026-03-${String((i % 28) + 1).padStart(2, "0")}`, "Pending", `k${i}`));
  const plans = planReminders(many, now);
  expect(plans).toHaveLength(MAX_REMINDERS);
  expect(plans.map((p) => p.at.getTime())).toEqual([...plans.map((p) => p.at.getTime())].sort((a, b) => a - b));
});
