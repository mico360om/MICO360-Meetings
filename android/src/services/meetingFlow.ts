/** The decisions behind the meeting screens: what a new meeting inherits, which
 *  template and company a meeting uses, how transcripts and minutes are saved,
 *  and which reminders are due. Screens call these and contain no rules. */
import type { ProgressFn } from "../core/pipeline";
import type { CompanyProfile } from "../core/profiles";
import { BUILTIN_PROMPTS, DEFAULT_STYLE } from "../core/prompts";
import { planReminders, type ReminderPlan } from "../core/reminders";
import { speakerText, type Transcript } from "../core/transcribeLong";
import { listActionItems } from "../data/actions";
import type { Db } from "../data/db";
import { getProfile, listMeetingTypes, listProfiles, listPrompts } from "../data/library";
import { getMeeting, type Meeting, type MeetingSource, updateMeeting } from "../data/meetings";
import { getSettings } from "../data/settings";
import { generateMinutesFor, type MinutesClient } from "./generation";

/** Prompt, style and company for a new meeting: from its type, else the settings. */
export async function newMeetingDefaults(db: Db, typeName?: string): Promise<Partial<Meeting>> {
  const s = await getSettings(db);
  const out: Partial<Meeting> = { meetingType: "", promptName: s.defaultPromptName, style: s.defaultStyle, profileId: s.defaultProfileId };
  const t = typeName ? (await listMeetingTypes(db)).find((x) => x.name === typeName) : undefined;
  if (!t) return out;
  out.meetingType = t.name;
  if (t.prompt_name) out.promptName = t.prompt_name;
  if (t.style) out.style = t.style;
  if (t.profile_name) {
    const p = (await listProfiles(db)).find((x) => x.name === t.profile_name);
    if (p) out.profileId = p.id;
  }
  return out;
}

/** The meeting's prompt by name; a deleted prompt falls back to the shipped
 *  prompt of that name, then to the default prompt. */
export async function templateFor(db: Db, m: Meeting): Promise<{ text: string; style: string }> {
  const s = await getSettings(db);
  const prompts = await listPrompts(db);
  const byName = (n: string) => prompts.find((p) => p.name === n)?.text ?? BUILTIN_PROMPTS.find((p) => p.name === n)?.text;
  const text = byName(m.promptName) ?? byName(s.defaultPromptName) ?? BUILTIN_PROMPTS[0].text;
  return { text, style: m.style || s.defaultStyle || DEFAULT_STYLE };
}

/** The meeting's company, or null (no branding) when none or deleted. */
export function profileFor(db: Db, m: Meeting): Promise<CompanyProfile | null> {
  return getProfile(db, m.profileId);
}

export async function saveTranscript(db: Db, id: string, t: Transcript, source?: MeetingSource): Promise<Meeting> {
  return updateMeeting(db, id, {
    transcript: t.text, segments: t.segments, language: t.language,
    ...(t.durationMs > 0 ? { durationMs: t.durationMs } : {}), ...(source ? { source } : {}),
  });
}

const running = new Set<string>();

/** Generate and save the minutes; one run per meeting at a time. */
export async function generateForMeeting(db: Db, id: string, client: MinutesClient,
  opts: { progress?: ProgressFn; signal?: AbortSignal } = {}): Promise<{ meeting: Meeting; warnings: string[] }> {
  if (running.has(id)) throw new Error("Minutes are already being generated for this meeting.");
  running.add(id);
  try {
    const m = await getMeeting(db, id);
    if (!m) throw new Error("This meeting no longer exists.");
    const { text, style } = await templateFor(db, m);
    const r = await generateMinutesFor(m, text, style, client, await getSettings(db), opts);
    const meeting = await updateMeeting(db, id, { minutes: r.minutes, model: r.model });
    return { meeting, warnings: r.warnings };
  } finally {
    running.delete(id);
  }
}

/** Put people's names on the speakers; blank names keep the label. */
export async function renameSpeakers(db: Db, id: string, names: Record<string, string>): Promise<Meeting> {
  const m = await getMeeting(db, id);
  if (!m) throw new Error("This meeting no longer exists.");
  const segments = m.segments.map((s) => ({ ...s, speaker: s.speaker ? names[s.speaker]?.trim() || s.speaker : s.speaker }));
  return updateMeeting(db, id, { segments, transcript: speakerText(segments) });
}

export async function reminderPlans(db: Db, now: Date): Promise<ReminderPlan[]> {
  if (!(await getSettings(db)).reminders) return [];
  return planReminders(await listActionItems(db), now);
}
