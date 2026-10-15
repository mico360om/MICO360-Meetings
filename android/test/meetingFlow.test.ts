import { beforeEach, describe, expect, it } from "vitest";
import { nodeDb } from "./nodeDb";
import type { Db } from "../src/data/db";
import { migrate, seed } from "../src/data/schema";
import { createMeeting, getMeeting } from "../src/data/meetings";
import { listActionItems, saveItemEdits, setItemStatus } from "../src/data/actions";
import { deleteProfile, listPrompts, saveMeetingType, saveProfile, updatePrompt } from "../src/data/library";
import { setSetting } from "../src/data/settings";
import { DEFAULT_PROFILE } from "../src/core/profiles";
import type { MinutesClient } from "../src/services/generation";
import * as F from "../src/services/meetingFlow";

let db: Db;
beforeEach(async () => { db = nodeDb(); await migrate(db); await seed(db); });

const TABLE = (rows: string[]) => ["## Action Items", "| Task | Owner | Deadline | Status |", "|---|---|---|---|", ...rows].join("\n");
function client(minutes: string, seen: string[] = [], gate?: Promise<void>): MinutesClient {
  return { model: "m1", warnings: [], isEncrypted: () => true,
    async generateMinutes(t, tpl) { seen.push(tpl); if (gate) await gate; return minutes; } };
}

describe("new meeting defaults", () => {
  it("come from the meeting type, with its company matched by name", async () => {
    const acme = await saveProfile(db, { ...DEFAULT_PROFILE, name: "Acme" });
    await saveMeetingType(db, { name: "Board", prompt_name: "Board Meeting Minutes", style: "Formal Minutes", profile_name: "Acme" });
    expect(await F.newMeetingDefaults(db, "Board")).toMatchObject({ meetingType: "Board", promptName: "Board Meeting Minutes",
      style: "Formal Minutes", profileId: acme.id });
  });
  it("otherwise from the settings", async () => {
    await setSetting(db, "defaultPromptName", "Short Summary");
    expect(await F.newMeetingDefaults(db)).toMatchObject({ meetingType: "", promptName: "Short Summary", profileId: "" });
  });
});

describe("template and company for a meeting", () => {
  it("uses the meeting's (edited) prompt", async () => {
    const formal = (await listPrompts(db)).find((p) => p.name === "Formal Minutes")!;
    await updatePrompt(db, formal.id, { text: "MY TEMPLATE [TRANSCRIPT_HERE]" });
    const m = await createMeeting(db, { promptName: "Formal Minutes", style: "Short Summary" });
    expect(await F.templateFor(db, m)).toEqual({ text: "MY TEMPLATE [TRANSCRIPT_HERE]", style: "Short Summary" });
  });
  it("a deleted prompt falls back to the shipped one, then to the default", async () => {
    await db.runAsync("DELETE FROM prompts WHERE name = 'Board Meeting Minutes'");
    const m1 = await createMeeting(db, { promptName: "Board Meeting Minutes" });
    expect((await F.templateFor(db, m1)).text).toContain("[TRANSCRIPT_HERE]");
    const m2 = await createMeeting(db, { promptName: "Gone forever" });
    const t2 = await F.templateFor(db, m2);
    expect(t2.text).toBe((await listPrompts(db)).find((p) => p.name === "Formal Minutes")!.text);
    expect(t2.style).toBe("Formal Minutes");
  });
  it("a deleted company means no branding", async () => {
    const p = await saveProfile(db, { ...DEFAULT_PROFILE, name: "Gone" });
    const m = await createMeeting(db, { profileId: p.id });
    await deleteProfile(db, p.id);
    expect(await F.profileFor(db, m)).toBeNull();
  });
});

describe("transcript and minutes", () => {
  it("the transcript is saved before generation and survives a failed generation", async () => {
    const m = await createMeeting(db, { title: "T" });
    await F.saveTranscript(db, m.id, { text: "We agreed.", language: "en", segments: [{ start: 0, end: 1, text: "We agreed." }], speakers: 0, durationMs: 1000 });
    const failing: MinutesClient = { ...client(""), async generateMinutes() { throw new Error("offline"); } };
    await expect(F.generateForMeeting(db, m.id, failing)).rejects.toThrow("offline");
    const again = (await getMeeting(db, m.id))!;
    expect(again).toMatchObject({ transcript: "We agreed.", language: "en", durationMs: 1000, minutes: "" });
  });
  it("saves the minutes and the model; only one run per meeting at a time", async () => {
    const m = await createMeeting(db, { title: "T", transcript: "We agreed." });
    let open!: () => void;
    const gate = new Promise<void>((r) => { open = r; });
    const first = F.generateForMeeting(db, m.id, client("# Minutes", [], gate));
    await expect(F.generateForMeeting(db, m.id, client("# Other"))).rejects.toThrow(/already being generated/);
    open();
    const r = await first;
    expect(r.meeting).toMatchObject({ minutes: "# Minutes", model: "m1" });
    await expect(F.generateForMeeting(db, m.id, client("# Again"))).resolves.toBeTruthy();
  });
  it("regenerated minutes keep the user's edits on unchanged action items", async () => {
    const m = await createMeeting(db, { title: "T", transcript: "x", minutes: TABLE(["| Send report | Ali | 2026-10-20 | Pending |"]) });
    await saveItemEdits(db, (await listActionItems(db))[0], { notes: "call first" });
    await F.generateForMeeting(db, m.id, client(TABLE(["| Book room | Sara | 2026-10-21 | Pending |", "| Send report | Ali | 2026-10-20 | Pending |"])));
    const items = await listActionItems(db);
    expect(items.map((i) => [i.task, i.notes])).toEqual([["Book room", ""], ["Send report", "call first"]]);
  });
  it("renaming speakers rewrites the transcript and keeps the names", async () => {
    const m = await createMeeting(db, { segments: [{ start: 0, end: 1, text: "Hi", speaker: "Speaker 1" }, { start: 1, end: 2, text: "Hello", speaker: "Speaker 2" }] });
    const r = await F.renameSpeakers(db, m.id, { "Speaker 1": "Ali", "Speaker 2": " " });
    expect(r.transcript).toBe("[Ali] Hi\n[Speaker 2] Hello");
    expect(r.segments.map((s) => s.speaker)).toEqual(["Ali", "Speaker 2"]);
  });
});

describe("reminders", () => {
  it("follow open items; completing one removes its reminders; off means none", async () => {
    await createMeeting(db, { title: "T", minutes: TABLE(["| Send report | Ali | 2026-10-20 | Pending |"]), createdAt: Date.UTC(2026, 9, 5) });
    const now = new Date(2026, 9, 15, 8, 0);
    expect(await F.reminderPlans(db, now)).toHaveLength(2);
    await setItemStatus(db, (await listActionItems(db))[0], "Completed");
    expect(await F.reminderPlans(db, now)).toEqual([]);
    await setItemStatus(db, (await listActionItems(db))[0], "Pending");
    await setSetting(db, "reminders", false);
    expect(await F.reminderPlans(db, now)).toEqual([]);
  });
});

describe("coming back to a meeting while its minutes are generated", () => {
  it("the running generation can be awaited, and is gone once finished", async () => {
    const m = await createMeeting(db, { title: "T", transcript: "x" });
    let open!: () => void;
    const gate = new Promise<void>((r) => { open = r; });
    const run = F.generateForMeeting(db, m.id, client("# Minutes", [], gate));
    const pending = F.generationInProgress(m.id);
    expect(pending).not.toBeNull();
    open();
    expect((await pending!).meeting.minutes).toBe("# Minutes");
    await run;
    expect(F.generationInProgress(m.id)).toBeNull();
  });
});

it("a default company that was deleted means no branding for new meetings", async () => {
  const p = await saveProfile(db, { ...DEFAULT_PROFILE, name: "Gone" });
  await setSetting(db, "defaultProfileId", p.id);
  await deleteProfile(db, p.id);
  expect((await F.newMeetingDefaults(db)).profileId).toBe("");
});

it("a transcription that found no speech is still marked as transcribed", async () => {
  const m = await createMeeting(db, { audioPath: "file:///r.wav" });
  const r = await F.saveTranscript(db, m.id, { text: "", language: "", segments: [], speakers: 0, durationMs: 0 });
  expect(F.isTranscribed(r)).toBe(true);
  expect(F.isTranscribed(m)).toBe(false);
});
