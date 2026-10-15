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
  const profiles = await listProfiles(db);
  const defaultProfile = profiles.some((p) => p.id === s.defaultProfileId) ? s.defaultProfileId : "";   // a deleted company: no branding
  const out: Partial<Meeting> = { meetingType: "", promptName: s.defaultPromptName, style: s.defaultStyle, profileId: defaultProfile };
  const t = typeName ? (await listMeetingTypes(db)).find((x) => x.name === typeName) : undefined;
  if (!t) return out;
  out.meetingType = t.name;
  if (t.prompt_name) out.promptName = t.prompt_name;
  if (t.style) out.style = t.style;
  if (t.profile_name) {
    const p = profiles.find((x) => x.name === t.profile_name);
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

/** True once the meeting's audio went through transcription (even if it held no speech). */
export const isTranscribed = (m: Pick<Meeting, "language" | "transcript">) => !!m.language || !!m.transcript.trim();

export async function saveTranscript(db: Db, id: string, t: Transcript, source?: MeetingSource): Promise<Meeting> {
  return updateMeeting(db, id, {
    transcript: t.text, segments: t.segments, language: t.language || "auto",   // never empty after a run: marks it transcribed
    ...(t.durationMs > 0 ? { durationMs: t.durationMs } : {}), ...(source ? { source } : {}),
  });
}

type Generated = { meeting: Meeting; warnings: string[] };
const running = new Map<string, Promise<Generated>>();

/** The generation still running for this meeting (a screen opened again can wait for it), or null. */
export function generationInProgress(id: string): Promise<Generated> | null {
  return running.get(id) ?? null;
}

/** Generate and save the minutes; one run per meeting at a time. */
export function generateForMeeting(db: Db, id: string, client: MinutesClient,
  opts: { progress?: ProgressFn; signal?: AbortSignal } = {}): Promise<Generated> {
  if (running.has(id)) return Promise.reject(new Error("Minutes are already being generated for this meeting."));
  const run = generate(db, id, client, opts).finally(() => running.delete(id));
  running.set(id, run);
  return run;
}

async function generate(db: Db, id: string, client: MinutesClient,
  opts: { progress?: ProgressFn; signal?: AbortSignal }): Promise<Generated> {
  {
    const m = await getMeeting(db, id);
    if (!m) throw new Error("This meeting no longer exists.");
    const { text, style } = await templateFor(db, m);
    const r = await generateMinutesFor(m, text, style, client, await getSettings(db), opts);
    const meeting = await updateMeeting(db, id, { minutes: r.minutes, model: r.model });
    return { meeting, warnings: r.warnings };
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
