/** Action items with deadlines as an RFC 5545 calendar (port of desktop
 *  core/calendar_ics.py): all-day events, optional reminder, stable UIDs. */
import { sha1 } from "@noble/hashes/legacy.js";
import { bytesToHex, utf8ToBytes } from "@noble/hashes/utils.js";
import { type ActionItem, deadlineDate, isPlaceholder, normalizeStatus, taskHash } from "./actionItems";
import { addDays } from "./dates";

const MAX_OCTETS = 75;

export function icsEscape(s: unknown): string {
  return String(s ?? "").split("\\").join("\\\\").split(";").join("\\;").split(",").join("\\,")
    .split("\r\n").join("\\n").split("\n").join("\\n").split("\r").join("\\n");
}

const utf8Len = (cp: number) => (cp < 0x80 ? 1 : cp < 0x800 ? 2 : cp < 0x10000 ? 3 : 4);

/** Fold by octets: 75 on the first line, a space + 74 on each continuation. */
export function foldLine(line: string): string {
  const out: string[] = [];
  let cur = "";
  let size = 0;
  for (const ch of line) {
    const n = utf8Len(ch.codePointAt(0)!);
    if (size + n > MAX_OCTETS) { out.push(cur); cur = " "; size = 1; }
    cur += ch;
    size += n;
  }
  out.push(cur);
  return out.join("\r\n");
}

export function eventUid(item: ActionItem): string {
  const key = item.okey || `${item.meetingId}:${taskHash(item.task)}:${item.occ}`;
  return bytesToHex(sha1(utf8ToBytes(`mico360-action:${key}`))).slice(0, 24) + "@mico360";
}

export const datedItems = (items: ActionItem[]) => items.filter((it) => deadlineDate(it) !== null);

const compact = (iso: string) => iso.split("-").join("");

export function buildIcs(items: ActionItem[], opts: { now?: Date; reminderDays?: number; calname?: string } = {}): string {
  const now = opts.now ?? new Date();
  const reminderDays = opts.reminderDays ?? 1;
  const stamp = now.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MICO360//Meetings//EN", "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH", `X-WR-CALNAME:${icsEscape(opts.calname ?? "MICO360 Action Items")}`];
  const seen = new Set<string>();
  for (const it of items) {
    const d = deadlineDate(it);
    if (d === null) continue;
    const owner = (it.owner ?? "").trim();
    const named = !!owner && !isPlaceholder(owner);
    const summary = named ? `[${owner}] ${it.task}` : it.task;
    const desc: string[] = [];
    if (named) desc.push(`Owner: ${owner}`);
    if (it.meetingTitle) desc.push(`Meeting: ${it.meetingTitle}`);
    if (it.priority) desc.push(`Priority: ${it.priority}`);
    desc.push(`Status: ${normalizeStatus(it.status)}`);
    let uid = eventUid(it);
    let n = 2;
    while (seen.has(uid)) { uid = eventUid(it).replace("@", `-${n}@`); n += 1; }
    seen.add(uid);
    lines.push("BEGIN:VEVENT", `UID:${uid}`, `DTSTAMP:${stamp}`, `DTSTART;VALUE=DATE:${compact(d)}`,
      `DTEND;VALUE=DATE:${compact(addDays(d, 1))}`, `SUMMARY:${icsEscape(summary)}`,
      "DESCRIPTION:" + desc.map(icsEscape).join("\\n"), "TRANSP:TRANSPARENT");
    if (reminderDays) {
      lines.push("BEGIN:VALARM", "ACTION:DISPLAY", `DESCRIPTION:${icsEscape("Reminder: " + it.task)}`,
        `TRIGGER:-P${reminderDays}D`, "END:VALARM");
    }
    lines.push("END:VEVENT");
  }
  lines.push("END:VCALENDAR");
  return lines.map(foldLine).join("\r\n") + "\r\n";
}
