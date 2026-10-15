import { type ActionItem, applyOverride, itemsForMeeting, type ItemOverride, normalizePriority, normalizeStatus } from "../core/actionItems";
import type { Db } from "./db";

interface Lite { id: string; title: string; created_at: number; minutes: string; }

function safeJson(s: string): unknown {
  try { return JSON.parse(s); } catch { return undefined; }
}

/** Every meeting's action items (newest meeting first) with the user's edits applied. */
export async function listActionItems(db: Db, search = ""): Promise<ActionItem[]> {
  const meetings = await db.getAllAsync<Lite>("SELECT id, title, created_at, minutes FROM meetings ORDER BY created_at DESC");
  const ovRows = await db.getAllAsync<{ okey: string; data: string }>("SELECT okey, data FROM action_overrides");
  const overrides = new Map(ovRows.map((r) => [r.okey, safeJson(r.data)]));
  const out: ActionItem[] = [];
  for (const m of meetings) {
    for (const it of itemsForMeeting({ id: m.id, title: m.title, createdAt: Number(m.created_at), minutes: m.minutes })) {
      applyOverride(it, overrides.get(it.okey) as ItemOverride | undefined);
      it.status = normalizeStatus(it.status);
      it.priority = normalizePriority(it.priority);
      out.push(it);
    }
  }
  const q = search.trim().toLowerCase();
  if (!q) return out;
  return out.filter((i) => [i.task, i.owner, i.meetingTitle, i.status, i.notes].some((v) => (v ?? "").toLowerCase().includes(q)));
}

async function currentOverride(db: Db, okey: string): Promise<ItemOverride> {
  const r = await db.getFirstAsync<{ data: string }>("SELECT data FROM action_overrides WHERE okey = ?", [okey]);
  const v = r ? safeJson(r.data) : undefined;
  return v && typeof v === "object" ? (v as ItemOverride) : {};
}

export async function saveItemEdits(db: Db, item: ActionItem, ov: ItemOverride): Promise<void> {
  const merged = { ...(await currentOverride(db, item.okey)), ...ov };
  if (merged.priority !== undefined) merged.priority = normalizePriority(merged.priority);
  await db.runAsync("INSERT OR REPLACE INTO action_overrides (okey, meeting_id, data) VALUES (?, ?, ?)",
    [item.okey, item.meetingId, JSON.stringify(merged)]);
}

export async function setItemStatus(db: Db, item: ActionItem, status: string): Promise<void> {
  await saveItemEdits(db, item, { status });
}

export async function resetItem(db: Db, item: ActionItem): Promise<void> {
  await db.runAsync("DELETE FROM action_overrides WHERE okey = ?", [item.okey]);
}
