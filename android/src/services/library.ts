/** Template (prompt) and company-profile import/export in the desktop's file
 *  formats. Re-importing a file never duplicates anything; a different entry
 *  with a taken name is renamed, never overwritten. */
import { sha1 } from "@noble/hashes/legacy.js";
import { bytesToHex } from "@noble/hashes/utils.js";
import { base64Bytes } from "../core/base64";
import { buildProfilesJson, type CompanyProfile, contentKey, copyProfileName, parseProfileFile, uniqueProfileName } from "../core/profiles";
import { buildPromptExport, parsePromptFile, planPromptImport } from "../core/prompts";
import type { Db } from "../data/db";
import { applyPromptImport, getProfile, listProfiles, listPrompts, saveProfile } from "../data/library";

/** Where logo images live (the app's documents folder on the phone). */
export interface LogoStore {
  save(base64: string, ext: string): Promise<string>;
  read(uri: string): Promise<string | null>;
  copy(uri: string): Promise<string>;
  remove(uri: string): Promise<void>;
}

export interface ImportSummary {
  imported: number; skipped: number; renamed: Array<[string, string]>; invalid: number; missingLogos: string[]; message: string;
}

/** Identity of a logo: its decoded bytes, so wrapping or padding of the base64 text doesn't matter. */
const digest = (b64: string | null) => (b64 ? bytesToHex(sha1(base64Bytes(b64))) : "");
const LOGO_EXTS = new Set(["png", "jpg", "jpeg", "gif", "bmp", "webp"]);
const BASE64_RE = /^[A-Za-z0-9+/]+={0,2}$/;

function summary(kind: string, s: Omit<ImportSummary, "message">): ImportSummary {
  const parts = [`Imported ${s.imported} ${kind}${s.imported === 1 ? "" : "s"}`];
  if (s.skipped) parts.push(`skipped ${s.skipped} already here`);
  if (s.renamed.length) parts.push("renamed " + s.renamed.slice(0, 3).map(([a, b]) => `"${a}" to "${b}"`).join(", "));
  if (s.missingLogos.length) parts.push(`logo not found for ${s.missingLogos.slice(0, 3).join(", ")}`);
  if (s.invalid) parts.push(`${s.invalid} unreadable entr${s.invalid === 1 ? "y" : "ies"} ignored`);
  return { ...s, message: parts.join("; ") + "." };
}

export async function importProfiles(db: Db, fileName: string, text: string, logos: LogoStore): Promise<ImportSummary> {
  const { records, invalid } = parseProfileFile(fileName, text);
  const existing = await listProfiles(db);
  const keys = new Set<string>();
  for (const p of existing) keys.add(contentKey(p, digest(p.logo_path ? await logos.read(p.logo_path) : null)));
  const names = existing.map((p) => p.name);
  const out = { imported: 0, skipped: 0, renamed: [] as Array<[string, string]>, invalid, missingLogos: [] as string[] };
  for (const r of records) {
    const clean = (r.logoData ?? "").replace(/\s+/g, "");
    const data = clean && BASE64_RE.test(clean) ? clean : "";
    const key = contentKey(r.profile, digest(data || null));
    if (keys.has(key)) { out.skipped += 1; continue; }
    keys.add(key);
    const name = uniqueProfileName(r.profile.name, names);
    if (name !== r.profile.name) out.renamed.push([r.profile.name, name]);
    let logoPath = "";
    if (data) {
      const ext = (r.logoName.split(".").pop() ?? "png").toLowerCase();
      logoPath = await logos.save(data, LOGO_EXTS.has(ext) ? ext : "png");
    } else if (r.logoPath) {
      out.missingLogos.push(name);
    }
    await saveProfile(db, { ...r.profile, id: "", name, logo_path: logoPath });
    names.push(name);
    out.imported += 1;
  }
  return summary("profile", out);
}

/** The chosen profiles (all when `ids` is empty) with their logos embedded. */
export async function exportProfiles(db: Db, ids: string[], logos: LogoStore): Promise<string> {
  const chosen = (await listProfiles(db)).filter((p) => !ids.length || ids.includes(p.id));
  const files: Record<string, { name: string; base64: string }> = {};
  for (const p of chosen) {
    const b64 = p.logo_path ? await logos.read(p.logo_path) : null;
    if (b64) files[p.id] = { name: p.logo_path.split("/").pop() ?? "logo.png", base64: b64 };
  }
  return buildProfilesJson(chosen, files);
}

export async function duplicateProfile(db: Db, id: string, logos: LogoStore): Promise<CompanyProfile | null> {
  const src = await getProfile(db, id);
  if (!src) return null;
  const name = copyProfileName(src.name, (await listProfiles(db)).map((p) => p.name));
  const logo = src.logo_path ? await logos.copy(src.logo_path) : "";
  return saveProfile(db, { ...src, id: "", name, logo_path: logo });
}

export async function importPrompts(db: Db, fileName: string, text: string): Promise<ImportSummary> {
  const { records, invalid } = parsePromptFile(fileName, text);
  const plan = planPromptImport(await listPrompts(db), records);
  await applyPromptImport(db, plan);
  return summary("prompt", { imported: plan.toAdd.length, skipped: plan.skipped.length, renamed: plan.renamed,
    invalid: plan.invalid + invalid, missingLogos: [] });
}

/** The chosen prompts (all when `ids` is empty) as a desktop prompt export. */
export async function exportPrompts(db: Db, ids: string[]): Promise<string> {
  return buildPromptExport((await listPrompts(db)).filter((p) => !ids.length || ids.includes(p.id)));
}
