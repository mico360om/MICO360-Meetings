/** Cross-meeting insights for the dashboard (port of desktop core/insights.py). */
import data from "./data/desktop.json";
import { type ActionItem, effectiveStatus, isOverdue, isPlaceholder, normalizeStatus } from "./actionItems";
import { type ISODate, addDays, localDateOf, todayLocal, weekday } from "./dates";
import { LETTER } from "./unicode";

export interface MeetingLite { id: string; title: string; createdAt: number; minutes: string; }
export interface Insights {
  totalMeetings: number; totalItems: number; openItems: number; overdueItems: number; completionRate: number;
  statusCounts: Record<string, number>; byOwner: Array<[string, number]>; cadence: Array<[string, number]>; keywords: Array<[string, number]>;
}

const STATUS_ORDER: string[] = data.statusOrder;
const STOPWORDS = new Set<string>(data.insightsStopwords);
const MARKS = new Set<number>(data.arabicMarks);
const WORD_RE = new RegExp(`[${LETTER}]+`, "gu");
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function themeWords(text: string): string[] {
  const t = Array.from((text ?? "").toLowerCase()).filter((ch) => !MARKS.has(ch.codePointAt(0)!)).join("");
  const out: string[] = [];
  for (const m of t.matchAll(WORD_RE)) {
    const w = m[0];
    if (STOPWORDS.has(w)) continue;
    if (w.length >= (/^[\x00-\x7f]*$/.test(w) ? 4 : 3)) out.push(w);
  }
  return out;
}

/** Counter.most_common: by count, ties in first-seen order. */
function mostCommon(keys: string[], top: number): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const k of keys) counts.set(k, (counts.get(k) ?? 0) + 1);
  return [...counts.entries()].map((e, i) => [e, i] as const)
    .sort((a, b) => b[0][1] - a[0][1] || a[1] - b[1]).slice(0, top).map(([e]) => e);
}

const weekStart = (d: ISODate) => addDays(d, -weekday(d));

export function computeInsights(meetings: MeetingLite[], items: ActionItem[],
  opts: { weeks?: number; topOwners?: number; topKeywords?: number; today?: ISODate } = {}): Insights {
  const today = opts.today ?? todayLocal();
  const weeks = opts.weeks ?? 8;
  const sc = new Map<string, number>();
  for (const it of items) { const s = effectiveStatus(it, today); sc.set(s, (sc.get(s) ?? 0) + 1); }
  const statusCounts: Record<string, number> = {};
  for (const k of STATUS_ORDER) if (sc.get(k)) statusCounts[k] = sc.get(k)!;
  const closed = (it: ActionItem) => ["Completed", "Cancelled"].includes(normalizeStatus(it.status));
  const openItems = items.filter((it) => !closed(it)).length;
  const overdueItems = items.filter((it) => isOverdue(it, today)).length;
  const nonCancelled = items.filter((it) => normalizeStatus(it.status) !== "Cancelled").length;
  const completed = items.filter((it) => normalizeStatus(it.status) === "Completed").length;

  const owners: string[] = [];
  for (const it of items) {
    if (closed(it)) continue;
    const o = (it.owner ?? "").trim();
    if (o && !isPlaceholder(o)) owners.push(o);
  }

  const cad = new Map<string, number>();
  for (const m of meetings) {
    if (!m.createdAt) continue;
    const w = weekStart(localDateOf(m.createdAt));
    cad.set(w, (cad.get(w) ?? 0) + 1);
  }
  const end = weekStart(today);
  const cadence: Array<[string, number]> = [];
  for (let i = weeks - 1; i >= 0; i--) {
    const w = addDays(end, -7 * i);
    cadence.push([`${w.slice(8, 10)} ${MON[Number(w.slice(5, 7)) - 1]}`, cad.get(w) ?? 0]);
  }

  const words = meetings.flatMap((m) => themeWords(m.minutes ?? ""));
  return {
    totalMeetings: meetings.length, totalItems: items.length, openItems, overdueItems,
    completionRate: nonCancelled ? completed / nonCancelled : 0, statusCounts,
    byOwner: mostCommon(owners, opts.topOwners ?? 8), cadence, keywords: mostCommon(words, opts.topKeywords ?? 12),
  };
}
