import type { MeetingType } from "../core/meetingTypes";
import { type CompanyProfile, sanitizeProfile } from "../core/profiles";
import { BUILTIN_PROMPTS, type PromptImportPlan, promptId, type SavedPrompt } from "../core/prompts";
import { type Db, newId } from "./db";

// ---- profiles ------------------------------------------------------------------
function parseProfile(data: string): CompanyProfile | null {
  try { return sanitizeProfile(JSON.parse(data) as CompanyProfile); } catch { return null; }
}

export async function listProfiles(db: Db): Promise<CompanyProfile[]> {
  const rows = await db.getAllAsync<{ data: string }>("SELECT data FROM profiles ORDER BY lower(name), id");
  return rows.map((r) => parseProfile(r.data)).filter((p): p is CompanyProfile => !!p);
}

export async function getProfile(db: Db, id: string): Promise<CompanyProfile | null> {
  if (!id) return null;
  const r = await db.getFirstAsync<{ data: string }>("SELECT data FROM profiles WHERE id = ?", [id]);
  return r ? parseProfile(r.data) : null;
}

export async function saveProfile(db: Db, profile: CompanyProfile): Promise<CompanyProfile> {
  const p = sanitizeProfile({ ...profile, id: profile.id || newId().slice(0, 10) });
  await db.runAsync("INSERT OR REPLACE INTO profiles (id, name, data) VALUES (?, ?, ?)", [p.id, p.name, JSON.stringify(p)]);
  return p;
}

export async function deleteProfile(db: Db, id: string): Promise<void> {
  await db.runAsync("DELETE FROM profiles WHERE id = ?", [id]);
}

// ---- prompts -------------------------------------------------------------------
interface PromptRow { id: string; name: string; category: string; text: string; builtin: number; favorite: number; }
const toPrompt = (r: PromptRow): SavedPrompt => ({ id: r.id, name: r.name, category: r.category, text: r.text,
  builtin: !!r.builtin, favorite: !!r.favorite });

export async function listPrompts(db: Db): Promise<SavedPrompt[]> {
  return (await db.getAllAsync<PromptRow>("SELECT * FROM prompts ORDER BY lower(category), lower(name)")).map(toPrompt);
}

export async function getPrompt(db: Db, id: string): Promise<SavedPrompt | null> {
  const r = await db.getFirstAsync<PromptRow>("SELECT * FROM prompts WHERE id = ?", [id]);
  return r ? toPrompt(r) : null;
}

export async function addPrompt(db: Db, p: { name: string; text: string; category?: string; favorite?: boolean }): Promise<SavedPrompt> {
  const name = p.name.trim() || "Untitled";
  const out: SavedPrompt = { id: promptId(name), name, text: p.text, category: p.category?.trim() || "General", builtin: false, favorite: !!p.favorite };
  await db.runAsync("INSERT INTO prompts (id, name, category, text, builtin, favorite) VALUES (?, ?, ?, ?, 0, ?)",
    [out.id, out.name, out.category, out.text, out.favorite ? 1 : 0]);
  return out;
}

/** A renamed built-in becomes the user's own prompt (the original is re-seeded). */
export async function updatePrompt(db: Db, id: string, patch: { name?: string; text?: string; category?: string }): Promise<SavedPrompt | null> {
  const cur = await getPrompt(db, id);
  if (!cur) return null;
  const name = patch.name?.trim() || cur.name;
  const next: SavedPrompt = { ...cur, name, text: patch.text ?? cur.text, category: patch.category ?? cur.category,
    builtin: cur.builtin && name === cur.name };
  await db.runAsync("UPDATE prompts SET name = ?, category = ?, text = ?, builtin = ? WHERE id = ?",
    [next.name, next.category, next.text, next.builtin ? 1 : 0, id]);
  return next;
}

export async function deletePrompt(db: Db, id: string): Promise<void> {
  await db.runAsync("DELETE FROM prompts WHERE id = ?", [id]);
}

export async function setPromptFavorite(db: Db, id: string, favorite: boolean): Promise<void> {
  await db.runAsync("UPDATE prompts SET favorite = ? WHERE id = ?", [favorite ? 1 : 0, id]);
}

/** Put a built-in prompt's shipped text back. */
export async function restorePrompt(db: Db, id: string): Promise<SavedPrompt | null> {
  const cur = await getPrompt(db, id);
  if (!cur || !cur.builtin) return null;
  const shipped = BUILTIN_PROMPTS.find((p) => p.name === cur.name);
  if (!shipped) return null;
  await db.runAsync("UPDATE prompts SET text = ?, category = ? WHERE id = ?", [shipped.text, shipped.category, id]);
  return { ...cur, text: shipped.text, category: shipped.category };
}

export async function applyPromptImport(db: Db, plan: PromptImportPlan): Promise<void> {
  await db.withTransactionAsync(async () => {
    for (const p of plan.toAdd) {
      await db.runAsync("INSERT INTO prompts (id, name, category, text, builtin, favorite) VALUES (?, ?, ?, ?, 0, ?)",
        [p.id, p.name, p.category, p.text, p.favorite ? 1 : 0]);
    }
  });
}

// ---- meeting types ---------------------------------------------------------------
export async function listMeetingTypes(db: Db): Promise<MeetingType[]> {
  return db.getAllAsync<MeetingType>("SELECT name, prompt_name, style, profile_name FROM meeting_types ORDER BY position, name");
}

export async function saveMeetingType(db: Db, t: MeetingType): Promise<void> {
  const name = t.name.trim();
  if (!name) throw new Error("A meeting type needs a name.");
  const pos = await db.getFirstAsync<{ p: number | null }>("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM meeting_types");
  await db.runAsync(
    `INSERT INTO meeting_types (name, prompt_name, style, profile_name, position) VALUES (?, ?, ?, ?, ?)
     ON CONFLICT(name) DO UPDATE SET prompt_name = excluded.prompt_name, style = excluded.style, profile_name = excluded.profile_name`,
    [name, t.prompt_name, t.style, t.profile_name, pos?.p ?? 0]);
}

export async function deleteMeetingType(db: Db, name: string): Promise<void> {
  await db.runAsync("DELETE FROM meeting_types WHERE name = ?", [name]);
}
