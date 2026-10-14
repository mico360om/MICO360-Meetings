/** Action items: extraction from minutes, statuses, deadlines, CSV
 *  (port of desktop core/tasks.py; persistence lives in the app layer). */
import { md5 } from "@noble/hashes/legacy.js";
import { bytesToHex, utf8ToBytes } from "@noble/hashes/utils.js";
import data from "./data/desktop.json";
import { type ISODate, addDays, fromIso, localDateOf, makeDate, todayLocal } from "./dates";
import { parse } from "./mdBlocks";
import { WORD } from "./unicode";

export const STATUS_CYCLE: string[] = data.statusCycle;
export const OVERDUE = "Overdue";
export const PRIORITIES = ["High", "Medium", "Low"];
const CLOSED = new Set(["Completed", "Cancelled"]);
const PLACEHOLDER = new Set<string>(data.placeholders);
const STATUS_SYN = new Map<string, string>(Object.entries(data.statusSynonyms));
const PRIO_SYN = new Map<string, string>(Object.entries(data.prioritySynonyms));
const BOM = String.fromCharCode(0xfeff);

export interface ActionItem {
  task: string; owner: string; deadline: string; status: string; priority: string; notes: string;
  meetingId: string; meetingTitle: string; meetingDate: string; okey: string; occ: number;
}
export type ItemOverride = Partial<Pick<ActionItem, "task" | "owner" | "deadline" | "priority" | "status" | "notes">>;

export const isPlaceholder = (s: string) => PLACEHOLDER.has((s ?? "").trim().toLowerCase());

export function normalizeStatus(status: string | null | undefined): string {
  const s = (status ?? "").trim();
  return STATUS_SYN.get(s.toLowerCase()) ?? (s || "Pending");
}

export function normalizePriority(value: string | null | undefined): string {
  const s = (value ?? "").trim();
  if (!s || PLACEHOLDER.has(s.toLowerCase())) return "";
  return PRIO_SYN.get(s.toLowerCase()) ?? s;
}

// ---- deadlines (Python strptime formats, case-insensitive) ------------------
const MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
  "september", "october", "november", "december"];
const MON3 = MONTHS.map((m) => m.slice(0, 3));
const TOKEN: Record<string, string> = {
  Y: "(\\d{4})", m: "(1[0-2]|0[1-9]|[1-9])", d: "(3[01]|[12]\\d|0[1-9]|[1-9]| [1-9])",
  b: `(${MON3.join("|")})`, B: `(${MONTHS.join("|")})`,
};
const DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y",
  "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%d %b, %Y", "%d %B, %Y"];
const YEARLESS_FORMATS = ["%d %b", "%d %B", "%b %d", "%B %d"];
const ORDINAL_RE = new RegExp(`(?<![${WORD}])(\\d{1,2})(st|nd|rd|th)(?![${WORD}])`, "giu");
const YEARLESS_PAST_GRACE_DAYS = 90;

function compile(fmt: string): { re: RegExp; fields: string[] } {
  let src = "";
  const fields: string[] = [];
  for (let i = 0; i < fmt.length; i++) {
    const ch = fmt[i];
    if (ch === "%") { const f = fmt[++i]; src += TOKEN[f]; fields.push(f); }
    else if (/\s/.test(ch)) { src += "\\s+"; while (i + 1 < fmt.length && /\s/.test(fmt[i + 1])) i++; }
    else src += ch.replace(/[.*+?^${}()|[\]\\/-]/g, "\\$&");
  }
  return { re: new RegExp(`^${src}$`, "i"), fields };
}
const COMPILED = DATE_FORMATS.map(compile);
const COMPILED_YEARLESS = YEARLESS_FORMATS.map((f) => compile(f + " %Y"));

function fieldsOf(c: { re: RegExp; fields: string[] }, s: string): { y: number; m: number; d: number } | null {
  const mt = c.re.exec(s);
  if (!mt) return null;
  let y = 0, m = 0, d = 0;
  c.fields.forEach((f, k) => {
    const v = mt[k + 1].trim().toLowerCase();
    if (f === "Y") y = Number(v);
    else if (f === "m") m = Number(v);
    else if (f === "d") d = Number(v);
    else if (f === "b") m = MON3.indexOf(v) + 1;
    else if (f === "B") m = MONTHS.indexOf(v) + 1;
  });
  return { y, m, d };
}

function resolveYear(month: number, day: number, ref: ISODate): ISODate | null {
  let year = Number(ref.slice(0, 4));
  const cand = makeDate(year, month, day);
  if (cand === null || cand < addDays(ref, -YEARLESS_PAST_GRACE_DAYS)) year += 1;
  for (let y = year; y < year + 8; y++) {
    const d = makeDate(y, month, day);
    if (d) return d;
  }
  return null;
}

/** Best-effort parse of a free-text deadline. `ref` (the meeting date) decides
 *  the year of a year-less deadline; it defaults to today. */
export function parseDeadline(text: string | null | undefined, ref?: ISODate | null): ISODate | null {
  let s = (text ?? "").trim();
  if (!s || PLACEHOLDER.has(s.toLowerCase())) return null;
  const iso = fromIso(s.slice(0, 10));
  if (iso) return iso;
  s = s.replace(ORDINAL_RE, (_m, n: string) => n).trim().replace(/\.+$/, "");
  s = s.replace(/\s+/g, " ");
  for (const c of COMPILED) {
    const f = fieldsOf(c, s);
    const d = f && makeDate(f.y, f.m, f.d);
    if (d) return d;
  }
  for (const c of COMPILED_YEARLESS) {
    const f = fieldsOf(c, `${s} 2000`);
    if (f && makeDate(2000, f.m, f.d)) return resolveYear(f.m, f.d, ref ?? todayLocal());
  }
  return null;
}

// ---- extraction ---------------------------------------------------------------
const clean = (v: string) => (v ?? "").split("**").join("").trim();

export function extractActionItems(minutesMd: string): ActionItem[] {
  const items: ActionItem[] = [];
  for (const blk of parse(minutesMd ?? "")) {
    if (blk.kind !== "table" || !blk.headers.length) continue;
    const hdr = blk.headers.map((h) => h.toLowerCase());
    if (!hdr.some((h) => h.includes("task") || h.includes("action"))) continue;
    const col = (keys: string[]) => hdr.findIndex((h) => keys.some((k) => h.includes(k)));
    const ciTask = col(["task", "action", "item"]);
    const ciOwner = col(["responsible", "person", "owner", "assign", "who"]);
    const ciDue = col(["deadline", "due", "date", "by when", "when"]);
    const ciStatus = col(["status", "state"]);
    const ciPrio = col(["priority", "importance", "urgency"]);
    for (const row of blk.rows) {
      const get = (ci: number) => (ci >= 0 && ci < row.length ? clean(row[ci]) : "");
      const task = get(ciTask);
      if (!task || PLACEHOLDER.has(task.toLowerCase())) continue;
      items.push({ task, owner: get(ciOwner), deadline: get(ciDue), status: get(ciStatus) || "Pending",
        priority: normalizePriority(get(ciPrio)), notes: "", meetingId: "", meetingTitle: "", meetingDate: "", okey: "", occ: 0 });
    }
  }
  return items;
}

export const taskHash = (task: string) => bytesToHex(md5(utf8ToBytes(task.trim().toLowerCase()))).slice(0, 10);

/** A meeting's items with stable per-row keys: meeting + task text + which
 *  occurrence of that text it is within the meeting. */
export function itemsForMeeting(m: { id: string; title: string; createdAt: number; minutes: string }): ActionItem[] {
  const seen = new Map<string, number>();
  return extractActionItems(m.minutes).map((it) => {
    const h = taskHash(it.task);
    const occ = seen.get(h) ?? 0;
    seen.set(h, occ + 1);
    return { ...it, meetingId: m.id, meetingTitle: m.title, meetingDate: m.createdAt ? localDateOf(m.createdAt) : "",
      occ, okey: `${m.id}:${h}:${occ}` };
  });
}

const EDITABLE = ["task", "owner", "deadline", "priority", "status", "notes"] as const;

export function applyOverride(item: ActionItem, ov: ItemOverride | string | null | undefined): ActionItem {
  if (!ov) return item;
  if (typeof ov === "string") { item.status = ov; return item; }
  for (const f of EDITABLE) if (ov[f] !== undefined && ov[f] !== null) item[f] = String(ov[f]);
  return item;
}

export function deadlineDate(item: ActionItem): ISODate | null {
  return parseDeadline(item.deadline, fromIso((item.meetingDate ?? "").slice(0, 10)));
}

export function isOverdue(item: ActionItem, today: ISODate = todayLocal()): boolean {
  if (CLOSED.has(normalizeStatus(item.status))) return false;
  const d = deadlineDate(item);
  return d !== null && d < today;
}

export function effectiveStatus(item: ActionItem, today: ISODate = todayLocal()): string {
  return isOverdue(item, today) ? OVERDUE : normalizeStatus(item.status);
}

export function reminderCounts(items: ActionItem[], today: ISODate = todayLocal(), withinDays = 7): { overdue: number; dueSoon: number } {
  let overdue = 0;
  let dueSoon = 0;
  const limit = addDays(today, withinDays);
  for (const it of items) {
    if (CLOSED.has(normalizeStatus(it.status))) continue;
    if (isOverdue(it, today)) { overdue += 1; continue; }
    const d = deadlineDate(it);
    if (d !== null && today <= d && d <= limit) dueSoon += 1;
  }
  return { overdue, dueSoon };
}

export function csvSafe(value: unknown): string {
  const s = value === null || value === undefined ? "" : String(value);
  return ["=", "+", "-", "@", "\t", "\r"].some((p) => s.startsWith(p)) ? "'" + s : s;
}

const csvField = (s: string) => (/[",\r\n]/.test(s) ? `"${s.split('"').join('""')}"` : s);

/** Excel-friendly CSV: UTF-8 BOM (Arabic intact), CRLF, every cell neutralised. */
export function buildActionItemsCsv(items: ActionItem[]): string {
  const rows = [["Task", "Responsible", "Deadline", "Priority", "Status", "Notes", "Meeting", "Meeting date"]];
  for (const i of items) {
    rows.push([i.task, i.owner, i.deadline, i.priority, i.status, i.notes, i.meetingTitle, i.meetingDate].map(csvSafe));
  }
  return BOM + rows.map((r) => r.map(csvField).join(",")).join("\r\n") + "\r\n";
}
