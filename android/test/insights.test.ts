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

it("words match regardless of case the way the desktop's casefold does (ß, final sigma, ligatures)", () => {
  expect(themeWords("Die Straße und STRASSE Straßen")).toEqual(["strasse", "strasse", "strassen"]);
  expect(themeWords("ΟΔΟΣ οδος Οδός")).toEqual(["οδοσ", "οδοσ", "οδόσ"]);
  expect(themeWords(String.fromCharCode(0xfb01) + "nancial FINANCIAL " + String.fromCharCode(0xfb02) + "ow")).toEqual(["financial", "financial", "flow"]);
});
