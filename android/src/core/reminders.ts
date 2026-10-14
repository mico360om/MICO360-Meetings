/** Local reminders for open action items: the day before and the day itself, 09:00. */
import { type ActionItem, deadlineDate, normalizeStatus } from "./actionItems";

export interface ReminderPlan { id: string; at: Date; title: string; body: string; }
export const REMINDER_HOUR = 9;
/** Android caps an app's pending alarms; the nearest ones matter most. */
export const MAX_REMINDERS = 50;

function at(iso: string, dayOffset: number): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d + dayOffset, REMINDER_HOUR, 0, 0, 0);
}

export function planReminders(items: ActionItem[], now: Date): ReminderPlan[] {
  const out: ReminderPlan[] = [];
  for (const it of items) {
    if (["Completed", "Cancelled"].includes(normalizeStatus(it.status))) continue;
    const d = deadlineDate(it);
    if (!d) continue;
    const body = [it.owner, it.meetingTitle].filter((x) => x && x.trim()).join(" · ");
    for (const [offset, label] of [[-1, "Due tomorrow"], [0, "Due today"]] as const) {
      const when = at(d, offset);
      if (when > now) out.push({ id: `${it.okey}:${offset}`, at: when, title: `${label}: ${it.task}`, body });
    }
  }
  return out.sort((a, b) => a.at.getTime() - b.at.getTime()).slice(0, MAX_REMINDERS);
}
