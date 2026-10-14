import { BUILTIN_MEETING_TYPES } from "../core/meetingTypes";
import { BUILTIN_PROMPTS, promptId } from "../core/prompts";
import type { Db } from "./db";

export const SCHEMA_VERSION = 1;

const V1 = `
CREATE TABLE IF NOT EXISTS meetings (
  id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  meeting_date TEXT NOT NULL DEFAULT '', attendees TEXT NOT NULL DEFAULT '', meeting_type TEXT NOT NULL DEFAULT '',
  profile_id TEXT NOT NULL DEFAULT '', prompt_name TEXT NOT NULL DEFAULT '', style TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '', audio_path TEXT NOT NULL DEFAULT '', duration_ms INTEGER NOT NULL DEFAULT 0,
  language TEXT NOT NULL DEFAULT '', transcript TEXT NOT NULL DEFAULT '', segments_json TEXT NOT NULL DEFAULT '[]',
  minutes TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft', model TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS meetings_created ON meetings (created_at DESC);
CREATE TABLE IF NOT EXISTS action_overrides (okey TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prompts (id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, text TEXT NOT NULL,
  builtin INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS meeting_types (name TEXT PRIMARY KEY, prompt_name TEXT NOT NULL DEFAULT '',
  style TEXT NOT NULL DEFAULT '', profile_name TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);`;

export async function migrate(db: Db): Promise<void> {
  const row = await db.getFirstAsync<{ user_version: number }>("PRAGMA user_version");
  const v = row?.user_version ?? 0;
  if (v >= SCHEMA_VERSION) return;
  if (v < 1) await db.execAsync(V1);
  await db.execAsync(`PRAGMA user_version = ${SCHEMA_VERSION}`);
}

/** Add any missing built-in prompt (by name) and, on first run, the meeting types. */
export async function seed(db: Db): Promise<void> {
  const names = new Set((await db.getAllAsync<{ name: string }>("SELECT name FROM prompts")).map((r) => r.name));
  for (const p of BUILTIN_PROMPTS) {
    if (names.has(p.name)) continue;
    await db.runAsync("INSERT INTO prompts (id, name, category, text, builtin, favorite) VALUES (?, ?, ?, ?, 1, 0)",
      [promptId(p.name), p.name, p.category, p.text]);
  }
  const types = await db.getFirstAsync<{ n: number }>("SELECT COUNT(*) AS n FROM meeting_types");
  if (!types?.n) {
    let pos = 0;
    for (const t of BUILTIN_MEETING_TYPES) {
      await db.runAsync("INSERT INTO meeting_types (name, prompt_name, style, profile_name, position) VALUES (?, ?, ?, ?, ?)",
        [t.name, t.prompt_name, t.style, t.profile_name, pos++]);
    }
  }
}
