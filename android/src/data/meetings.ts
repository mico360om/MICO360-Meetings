import { type Db, newId, type SqlParam } from "./db";

export interface TranscriptSegment { start: number; end: number; text: string; speaker?: string; }
export type MeetingSource = "record" | "import" | "paste" | "";

export interface Meeting {
  id: string; title: string; createdAt: number; updatedAt: number; meetingDate: string; attendees: string;
  meetingType: string; profileId: string; promptName: string; style: string; source: MeetingSource;
  audioPath: string; durationMs: number; language: string; transcript: string; segments: TranscriptSegment[];
  minutes: string; status: "draft" | "final"; model: string;
}

interface Row {
  id: string; title: string; created_at: number; updated_at: number; meeting_date: string; attendees: string;
  meeting_type: string; profile_id: string; prompt_name: string; style: string; source: string; audio_path: string;
  duration_ms: number; language: string; transcript: string; segments_json: string; minutes: string; status: string; model: string;
}

const COLUMNS: Array<[keyof Meeting, keyof Row]> = [
  ["title", "title"], ["createdAt", "created_at"], ["updatedAt", "updated_at"], ["meetingDate", "meeting_date"],
  ["attendees", "attendees"], ["meetingType", "meeting_type"], ["profileId", "profile_id"], ["promptName", "prompt_name"],
  ["style", "style"], ["source", "source"], ["audioPath", "audio_path"], ["durationMs", "duration_ms"],
  ["language", "language"], ["transcript", "transcript"], ["minutes", "minutes"], ["status", "status"], ["model", "model"],
];

function parseSegments(json: string): TranscriptSegment[] {
  try { const v = JSON.parse(json); return Array.isArray(v) ? v : []; } catch { return []; }
}

function fromRow(r: Row): Meeting {
  return {
    id: r.id, title: r.title, createdAt: Number(r.created_at), updatedAt: Number(r.updated_at), meetingDate: r.meeting_date,
    attendees: r.attendees, meetingType: r.meeting_type, profileId: r.profile_id, promptName: r.prompt_name, style: r.style,
    source: (r.source as MeetingSource) || "", audioPath: r.audio_path, durationMs: Number(r.duration_ms), language: r.language,
    transcript: r.transcript, segments: parseSegments(r.segments_json), minutes: r.minutes,
    status: r.status === "final" ? "final" : "draft", model: r.model,
  };
}

const value = (m: Partial<Meeting>, k: keyof Meeting): SqlParam => {
  const v = m[k];
  return typeof v === "number" || typeof v === "string" ? v : v === undefined || v === null ? null : String(v);
};

export async function createMeeting(db: Db, init: Partial<Meeting>): Promise<Meeting> {
  const now = Date.now();
  const m: Meeting = {
    id: newId(), title: "", createdAt: now, updatedAt: now, meetingDate: "", attendees: "", meetingType: "", profileId: "",
    promptName: "", style: "", source: "", audioPath: "", durationMs: 0, language: "", transcript: "", segments: [],
    minutes: "", status: "draft", model: "", ...init,
  };
  m.updatedAt = Math.max(m.updatedAt, m.createdAt);
  await db.runAsync(
    `INSERT INTO meetings (id, ${COLUMNS.map(([, c]) => c).join(", ")}, segments_json) VALUES (?, ${COLUMNS.map(() => "?").join(", ")}, ?)`,
    [m.id, ...COLUMNS.map(([k]) => value(m, k)), JSON.stringify(m.segments)]);
  return m;
}

export async function getMeeting(db: Db, id: string): Promise<Meeting | null> {
  const r = await db.getFirstAsync<Row>("SELECT * FROM meetings WHERE id = ?", [id]);
  return r ? fromRow(r) : null;
}

/** Write only the patched columns, so two saves in flight (an autosave and a
 *  finishing generation) never overwrite each other's fields. */
export async function updateMeeting(db: Db, id: string, patch: Partial<Omit<Meeting, "id" | "createdAt">>): Promise<Meeting> {
  const sets: string[] = ["updated_at = MAX(updated_at, ?)"];
  const params: SqlParam[] = [Date.now()];
  for (const [k, c] of COLUMNS) {
    if (k === "updatedAt" || k === "createdAt" || !(k in patch)) continue;
    sets.push(`${c} = ?`);
    params.push(value(patch, k));
  }
  if ("segments" in patch) { sets.push("segments_json = ?"); params.push(JSON.stringify(patch.segments ?? [])); }
  const r = await db.runAsync(`UPDATE meetings SET ${sets.join(", ")} WHERE id = ?`, [...params, id]);
  if (!r.changes) throw new Error("This meeting no longer exists.");
  return (await getMeeting(db, id))!;
}

const BACKSLASH = String.fromCharCode(92);
/** Escape for LIKE ... ESCAPE: the escape character itself first, then % and _. */
const likeEscape = (s: string) => s.split(BACKSLASH).join(BACKSLASH + BACKSLASH).replace(/[%_]/g, (c) => BACKSLASH + c);

export async function listMeetings(db: Db, f: { search?: string; status?: "draft" | "final" } = {}): Promise<Meeting[]> {
  const where: string[] = [];
  const params: SqlParam[] = [];
  const q = (f.search ?? "").trim();
  if (q) {
    const like = (col: string) => `${col} LIKE ? ESCAPE '${BACKSLASH}'`;
    where.push(`(${["title", "transcript", "minutes", "attendees"].map(like).join(" OR ")})`);
    const p = `%${likeEscape(q)}%`;
    params.push(p, p, p, p);
  }
  if (f.status) { where.push("status = ?"); params.push(f.status); }
  const rows = await db.getAllAsync<Row>(
    `SELECT * FROM meetings ${where.length ? "WHERE " + where.join(" AND ") : ""} ORDER BY created_at DESC`, params);
  return rows.map(fromRow);
}

export async function deleteMeeting(db: Db, id: string): Promise<void> {
  await db.withTransactionAsync(async () => {
    await db.runAsync("DELETE FROM action_overrides WHERE meeting_id = ?", [id]);
    await db.runAsync("DELETE FROM meetings WHERE id = ?", [id]);
  });
}
