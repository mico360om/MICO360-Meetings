import { beforeEach, describe, expect, it } from "vitest";
import { nodeDb } from "./nodeDb";
import type { Db } from "../src/data/db";
import { migrate, seed } from "../src/data/schema";
import { listProfiles, listPrompts } from "../src/data/library";
import * as S from "../src/services/library";

let db: Db;
let files: Map<string, string>;
let seq = 0;
const logos: S.LogoStore = {
  async save(b64, ext) { const uri = `file:///logos/${seq++}.${ext}`; files.set(uri, b64); return uri; },
  async read(uri) { return files.get(uri) ?? null; },
  async copy(uri) { const uri2 = `file:///logos/${seq++}.png`; files.set(uri2, files.get(uri) ?? ""); return uri2; },
  async remove(uri) { files.delete(uri); },
};
beforeEach(async () => { db = nodeDb(); await migrate(db); await seed(db); files = new Map(); });

const DESKTOP_EXPORT = JSON.stringify([{ id: "x1", name: "Acme", phone: 123, pdf_design: "Modern", logo_path: "C:/a/logo.png",
  logo_name: "logo.png", logo_data: "iVBORw0KGgo=" }]);

describe("profiles", () => {
  it("imports a desktop export with its logo; importing it again changes nothing", async () => {
    const first = await S.importProfiles(db, "profiles.json", DESKTOP_EXPORT, logos);
    expect(first.imported).toBe(1);
    const [p] = await listProfiles(db);
    expect(p).toMatchObject({ name: "Acme", phone: "123", pdf_design: "modern" });
    expect(files.get(p.logo_path)).toBe("iVBORw0KGgo=");
    const again = await S.importProfiles(db, "profiles.json", DESKTOP_EXPORT, logos);
    expect(again).toMatchObject({ imported: 0, skipped: 1 });
    expect(await listProfiles(db)).toHaveLength(1);
    expect(files.size).toBe(1);
  });
  it("a different profile with a taken name is renamed, never overwritten", async () => {
    await S.importProfiles(db, "a.json", DESKTOP_EXPORT, logos);
    const r = await S.importProfiles(db, "b.json", JSON.stringify([{ name: "Acme", phone: "999" }]), logos);
    expect(r.renamed).toEqual([["Acme", "Acme (2)"]]);
    expect(r.message).toMatch(/renamed "Acme" to "Acme \(2\)"/);
  });
  it("a CSV row naming a logo file we don't have says so", async () => {
    const r = await S.importProfiles(db, "p.csv", "name,logo_path\nBeta,D:/x/beta.png\n", logos);
    expect(r.missingLogos).toEqual(["Beta"]);
  });
  it("export embeds logos; duplicate copies the logo file", async () => {
    await S.importProfiles(db, "a.json", DESKTOP_EXPORT, logos);
    const [p] = await listProfiles(db);
    const out = JSON.parse(await S.exportProfiles(db, [p.id], logos));
    expect(out[0]).toMatchObject({ name: "Acme", logo_data: "iVBORw0KGgo=" });
    const copy = (await S.duplicateProfile(db, p.id, logos))!;
    expect(copy.name).toBe("Acme (copy)");
    expect(copy.logo_path).not.toBe(p.logo_path);
    expect(files.get(copy.logo_path)).toBe("iVBORw0KGgo=");
  });
});

describe("prompts", () => {
  it("round-trip through the desktop format; a second import skips", async () => {
    const file = JSON.stringify({ mico360_prompts: 1, prompts: [{ name: "Weekly", category: "Summary", text: "Do [TRANSCRIPT_HERE]" }] });
    const r = await S.importPrompts(db, "p.json", file);
    expect(r.message).toMatch(/^Imported 1 prompt/);
    expect((await S.importPrompts(db, "p.json", file)).skipped).toBe(1);
    const weekly = (await listPrompts(db)).find((p) => p.name === "Weekly")!;
    expect(JSON.parse(await S.exportPrompts(db, [weekly.id])).prompts[0].text).toBe("Do [TRANSCRIPT_HERE]");
  });
});
