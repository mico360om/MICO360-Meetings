import type { GenerateOptions } from "../core/cloudClient";
import type { Meeting } from "../data/meetings";
import type { AppSettings } from "../data/settings";

export class ConsentRequired extends Error {
  constructor() {
    super("MICO360 Cloud is reached over an unencrypted connection. Your transcript would be sent unencrypted.");
    this.name = "ConsentRequired";
  }
}

/** What generation needs from CloudClient (tests use a fake). */
export interface MinutesClient {
  model: string;
  warnings: string[];
  isEncrypted(): boolean;
  generateMinutes(transcript: string, template: string, o?: GenerateOptions): Promise<string>;
}

/** Desktop calendar_import.format_preamble: only real values. */
export function meetingPreamble(m: Pick<Meeting, "title" | "meetingDate" | "attendees">): string {
  const parts: string[] = [];
  if (m.title.trim()) parts.push(`Meeting Title: ${m.title.trim()}`);
  if (m.meetingDate.trim()) parts.push(`Date and Time: ${m.meetingDate.trim()}`);
  const att = m.attendees.split(",").map((a) => a.trim()).filter(Boolean);
  if (att.length) parts.push("Attendees: " + att.join(", "));
  if (!parts.length) return "";
  return "Known meeting details (use these exactly in the minutes header):\n" + parts.join("\n") + "\n\n";
}

export async function generateMinutesFor(meeting: Meeting, template: string, style: string, client: MinutesClient,
  settings: AppSettings, opts: Pick<GenerateOptions, "progress" | "signal"> = {}): Promise<{ minutes: string; warnings: string[]; model: string }> {
  if (!meeting.transcript.trim()) throw new Error("This meeting has no transcript yet — record, import or paste one first.");
  if (!client.isEncrypted() && !settings.plainHttpConsent) throw new ConsentRequired();
  const minutes = await client.generateMinutes(meetingPreamble(meeting) + meeting.transcript, template, {
    style, removeFillers: settings.removeFillers, chunkChars: settings.chunkChars, progress: opts.progress, signal: opts.signal,
  });
  return { minutes, warnings: [...client.warnings], model: client.model };
}
