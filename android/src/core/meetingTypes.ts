/** Meeting-type presets: a prompt + output style + optional company, by name
 *  (port of desktop core/meeting_types.py). */
import data from "./data/desktop.json";

export interface MeetingType { name: string; prompt_name: string; style: string; profile_name: string; }

export const BUILTIN_MEETING_TYPES: MeetingType[] = data.meetingTypes.map((t) => ({ ...t }));

/** Keep only well-formed entries (a bad one is skipped, not the whole list). */
export function sanitizeMeetingTypes(raw: unknown): MeetingType[] {
  if (!Array.isArray(raw)) return [];
  const out: MeetingType[] = [];
  for (const d of raw) {
    if (!d || typeof d !== "object" || Array.isArray(d)) continue;
    const r = d as Record<string, unknown>;
    const s = (k: string) => (r[k] === null || r[k] === undefined ? "" : String(r[k]));
    if (!s("name").trim()) continue;
    out.push({ name: s("name"), prompt_name: s("prompt_name"), style: s("style"), profile_name: s("profile_name") });
  }
  return out;
}
