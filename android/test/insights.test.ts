import { expect, it } from "vitest";
import fx from "./fixtures/insights.json";
import type { ActionItem } from "../src/core/actionItems";
import { computeInsights, themeWords } from "../src/core/insights";

const items: ActionItem[] = fx.items.map((i) => ({
  task: String(i.task), owner: String(i.owner), deadline: String(i.deadline), status: String(i.status), priority: String(i.priority),
  notes: String(i.notes), meetingId: String(i.meeting_id), meetingTitle: String(i.meeting_title), meetingDate: String(i.meeting_date),
  okey: String(i.okey), occ: Number(i.occ) }));

it("matches desktop compute_insights", () => {
  expect(computeInsights(fx.meetings, items, { today: fx.today })).toEqual(fx.result);
});

it("theme words: any script, stopwords and Arabic diacritics removed", () => {
  expect(themeWords("The meeting discussed BUDGET budgets; الْمِيزَانِيَّة على")).toEqual(["budget", "budgets", "الميزانية"]);
});
