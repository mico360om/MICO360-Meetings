import { beforeEach, describe, expect, it } from "vitest";
import { nodeDb } from "./nodeDb";
import type { Db } from "../src/data/db";
import { migrate, seed } from "../src/data/schema";
import { createMeeting, deleteMeeting, getMeeting, listMeetings, updateMeeting } from "../src/data/meetings";
import { listActionItems, resetItem, saveItemEdits, setItemStatus } from "../src/data/actions";
import * as L from "../src/data/library";
import { getSettings, setSetting } from "../src/data/settings";
import { DEFAULT_PROFILE } from "../src/core/profiles";

const MINUTES = "## Action Items\n| Task | Owner | Deadline | Status |\n|---|---|---|---|\n| Send report | Ali | 2026-10-01 | Pending |\n| Send report | Bob | 2026-10-30 | Done |";
let db: Db;

beforeEach(async () => { db = nodeDb(); await migrate(db); await seed(db); });

describe("schema", () => {
  it("migrates once and seeds the built-ins once", async () => {
    await migrate(db); await seed(db);
    expect((await db.getFirstAsync<{ user_version: number }>("PRAGMA user_version"))!.user_version).toBe(1);
    expect(await L.listPrompts(db)).toHaveLength(25);
    expect(await L.listMeetingTypes(db)).toHaveLength(6);
  });
});

describe("meetings", () => {
  it("create, read, update (updated_at moves), search, delete", async () => {
    const m = await createMeeting(db, { title: "Budget review", transcript: "We discussed the budget." });
    expect(m.id).toMatch(/^[0-9a-f]{16}$/);
    expect(m.status).toBe("draft");
    const u = await updateMeeting(db, m.id, { minutes: MINUTES, segments: [{ start: 0, end: 1, text: "Hi", speaker: "Speaker 1" }] });
    expect(u.updatedAt).toBeGreaterThanOrEqual(m.updatedAt);
    expect((await getMeeting(db, m.id))!.segments[0].speaker).toBe("Speaker 1");
    await createMeeting(db, { title: "Other" });
    expect((await listMeetings(db, { search: "budget" })).map((x) => x.title)).toEqual(["Budget review"]);
    expect(await listMeetings(db, { search: "100%_" })).toEqual([]);
    await deleteMeeting(db, m.id);
    expect(await getMeeting(db, m.id)).toBeNull();
  });
});

describe("action items", () => {
  it("come from the minutes and keep their own edits per row", async () => {
    const m = await createMeeting(db, { title: "Budget", minutes: MINUTES, createdAt: Date.UTC(2026, 9, 5, 12) });
    let items = await listActionItems(db);
    expect(items.map((i) => [i.owner, i.status])).toEqual([["Ali", "Pending"], ["Bob", "Completed"]]);
    await setItemStatus(db, items[0], "In Progress");
    await saveItemEdits(db, items[1], { owner: "Sara", notes: "call first" });
    items = await listActionItems(db);
    expect(items.map((i) => [i.owner, i.status, i.notes])).toEqual([["Ali", "In Progress", ""], ["Sara", "Completed", "call first"]]);
    expect(await listActionItems(db, "sara")).toHaveLength(1);
    await resetItem(db, items[1]);
    expect((await listActionItems(db))[1].owner).toBe("Bob");
    await deleteMeeting(db, m.id);
    expect(await db.getAllAsync("SELECT * FROM action_overrides")).toEqual([]);
  });
});

describe("library", () => {
  it("profiles are sanitised and sorted", async () => {
    await L.saveProfile(db, { ...DEFAULT_PROFILE, name: "beta", accent_color: "javascript:x" });
    const a = await L.saveProfile(db, { ...DEFAULT_PROFILE, name: "Alpha" });
    const list = await L.listProfiles(db);
    expect(list.map((p) => p.name)).toEqual(["Alpha", "beta"]);
    expect(list[1].accent_color).toBe("#8B1E1E");
    expect((await L.getProfile(db, a.id))!.name).toBe("Alpha");
    await L.deleteProfile(db, a.id);
    expect(await L.getProfile(db, a.id)).toBeNull();
  });
  it("a renamed built-in prompt becomes the user's own; restore puts the text back", async () => {
    const formal = (await L.listPrompts(db)).find((p) => p.name === "Formal Minutes")!;
    await L.updatePrompt(db, formal.id, { text: "edited" });
    expect((await L.getPrompt(db, formal.id))!.builtin).toBe(true);
    await L.restorePrompt(db, formal.id);
    expect((await L.getPrompt(db, formal.id))!.text).toContain("[TRANSCRIPT_HERE]");
    await L.updatePrompt(db, formal.id, { name: "My formal" });
    expect((await L.getPrompt(db, formal.id))!.builtin).toBe(false);
    await seed(db);
    expect((await L.listPrompts(db)).some((p) => p.name === "Formal Minutes" && p.builtin)).toBe(true);
  });
  it("meeting types upsert by name and keep their order", async () => {
    await L.saveMeetingType(db, { name: "Weekly", prompt_name: "Short Summary", style: "Short Summary", profile_name: "" });
    await L.saveMeetingType(db, { name: "Weekly", prompt_name: "Decision Log", style: "", profile_name: "" });
    const types = await L.listMeetingTypes(db);
    expect(types).toHaveLength(7);
    expect(types[6]).toMatchObject({ name: "Weekly", prompt_name: "Decision Log" });
    await L.deleteMeetingType(db, "Weekly");
    expect(await L.listMeetingTypes(db)).toHaveLength(6);
  });
});

describe("settings", () => {
  it("defaults, typed writes, unknown junk ignored", async () => {
    expect((await getSettings(db)).whisperModel).toBe("base");
    await setSetting(db, "diarize", true);
    await db.runAsync("INSERT INTO settings (key, value) VALUES ('chunkChars', 'not json')");
    const s = await getSettings(db);
    expect(s.diarize).toBe(true);
    expect(s.chunkChars).toBe(6000);
  });
});
