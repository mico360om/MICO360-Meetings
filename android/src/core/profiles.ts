/** Company profiles: branding + layout for exports (port of desktop
 *  core/profiles.py). Keys stay snake_case so files move between the apps. */
import data from "./data/desktop.json";
import { parseCsv } from "./csv";

export interface CompanyProfile {
  id: string; name: string; address: string; phone: string; email: string; website: string;
  logo_path: string; logo_position: string; logo_width_mm: number; footer_text: string; footer_alignment: string;
  show_page_numbers: boolean; page_number_position: string; page_number_format: string; accent_color: string; pdf_design: string;
}

export const DEFAULT_PROFILE: CompanyProfile = { ...(data.profileDefaults as CompanyProfile) };
export const PDF_DESIGNS: string[] = data.pdfDesigns.map((d) => d.key);
const DEFAULT_PDF_DESIGN: string = data.defaultPdfDesign;
const ALIGNMENTS = ["left", "center", "right"];
const PAGENUM_POSITIONS = ["footer-left", "footer-center", "footer-right", "header-left", "header-center", "header-right"];
const FIELD_NAMES = new Set(Object.keys(DEFAULT_PROFILE));
const COLOR_RE = /^#[0-9A-Fa-f]{3,8}$/;
const HEADER_ALIASES: Record<string, string> = {
  company: "name", company_name: "name", organisation: "name", organization: "name", footer: "footer_text",
  logo: "logo_path", logo_file: "logo_path", accent: "accent_color", accent_colour: "accent_color",
  colour: "accent_color", color: "accent_color", telephone: "phone", tel: "phone", e_mail: "email", web: "website",
  url: "website", page_numbers: "show_page_numbers", logo_width: "logo_width_mm", design: "pdf_design",
  pdf_layout: "pdf_design", pdf_template: "pdf_design", layout: "pdf_design", template: "pdf_design",
};
const POSITION_ALIASES: Record<string, string> = { centre: "center", middle: "center", centered: "center", centred: "center" };

/** Python-like str() for the values JSON/CSV hand us. */
function pyStr(v: unknown): string {
  if (typeof v === "boolean") return v ? "True" : "False";
  return String(v);
}

/** Python's "value or ''": falsy values become the empty string. */
const orEmpty = (v: unknown) => (v === null || v === undefined || v === false || v === 0 || v === "" ? "" : pyStr(v));

export function choice(value: unknown, allowed: string[], def: string): string {
  let s = orEmpty(value).trim().toLowerCase().replace(/[\s_]+/g, "-");
  s = s.split("-").map((p) => POSITION_ALIASES[p] ?? p).join("-");
  s = s.split("top-").join("header-").split("bottom-").join("footer-");
  return allowed.includes(s) ? s : def;
}

function pyFloat(v: unknown): number | null {
  if (typeof v === "number") return v;
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v !== "string") return null;
  const s = v.trim().toLowerCase().split("_").join("");
  if (/^[+-]?(inf|infinity)$/.test(s)) return s.startsWith("-") ? -Infinity : Infinity;
  if (/^[+-]?nan$/.test(s)) return NaN;
  if (!/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/.test(s)) return null;
  return Number(s);
}

export function coerce(key: string, value: unknown): string | number | boolean {
  if (key === "logo_position" || key === "footer_alignment") return choice(value, ALIGNMENTS, key === "logo_position" ? "left" : "center");
  if (key === "page_number_position") return choice(value, PAGENUM_POSITIONS, "footer-right");
  if (key === "pdf_design") return choice(value, PDF_DESIGNS, DEFAULT_PDF_DESIGN);
  if (key === "page_number_format") {
    const s = value === null || value === undefined ? "" : pyStr(value).trim();
    return s || "Page {n} of {total}";
  }
  if (key === "accent_color") {
    const s = orEmpty(value).trim();
    return COLOR_RE.test(s) ? s : "#8B1E1E";             // it ends up in CSS: #hex only
  }
  if (key === "show_page_numbers") {
    if (typeof value === "boolean") return value;
    const s = value === null || value === undefined ? "" : pyStr(value).trim().toLowerCase();
    if (!s) return true;
    return ["1", "true", "yes", "y", "on"].includes(s);
  }
  if (key === "logo_width_mm") {
    const v = pyFloat(value);
    if (v === null || Number.isNaN(v)) return 35.0;
    return Math.min(Math.max(v, 10.0), 90.0);
  }
  if (value === null || value === undefined) return "";
  if (typeof value === "number" && Number.isInteger(value)) return String(value);
  return pyStr(value).trim();
}

export function normHeader(h: unknown): string {
  const k = orEmpty(h).trim().toLowerCase().replace(/[\s-]+/g, "_");
  return HEADER_ALIASES[k] ?? k;
}

export function normalizeRecord(rec: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(rec)) {
    const nk = normHeader(k);
    if (FIELD_NAMES.has(nk) && (!(nk in out) || out[nk] === null || out[nk] === "")) out[nk] = v;
  }
  const meaningful = Object.entries(out).some(([k, v]) => k !== "id" && v !== null && v !== undefined && pyStr(v).trim() !== "");
  return meaningful ? out : {};
}

export function profileFromRecord(rec: Record<string, unknown>): CompanyProfile {
  const p: Record<string, unknown> = { ...DEFAULT_PROFILE };
  for (const [k, v] of Object.entries(rec)) if (FIELD_NAMES.has(k)) p[k] = coerce(k, v);
  return p as unknown as CompanyProfile;
}

/** What a save stores: every field coerced, never a blank name. */
export function sanitizeProfile(profile: CompanyProfile): CompanyProfile {
  const p: Record<string, unknown> = { ...profile };
  for (const k of FIELD_NAMES) if (k !== "id") p[k] = coerce(k, p[k]);
  if (!String(p.name ?? "").trim()) p.name = "Company";
  return p as unknown as CompanyProfile;
}

export function uniqueProfileName(name: string, taken: string[]): string {
  const t = new Set(taken.map((x) => x.trim().toLowerCase()));
  const base = (name ?? "").trim() || "Company";
  if (!t.has(base.toLowerCase())) return base;
  let n = 2;
  while (t.has(`${base} (${n})`.toLowerCase())) n += 1;
  return `${base} (${n})`;
}

export function copyProfileName(name: string, taken: string[]): string {
  const t = new Set(taken.map((x) => x.trim().toLowerCase()));
  let cand = `${name} (copy)`;
  let n = 2;
  while (t.has(cand.toLowerCase())) { cand = `${name} (copy ${n})`; n += 1; }
  return cand;
}

/** Identity of a profile's CONTENT (not its id or where its logo lives). */
export function contentKey(p: CompanyProfile, logoDigest: string): string {
  const d: Record<string, unknown> = { ...p };
  delete d.id;
  delete d.logo_path;
  const sorted = Object.keys(d).sort().map((k) => [k, d[k]]);
  return JSON.stringify(sorted) + "|" + logoDigest;
}

export interface ProfileImportRecord { profile: CompanyProfile; logoData: string; logoName: string; logoPath: string; }

export function parseProfileFile(fileName: string, text: string): { records: ProfileImportRecord[]; invalid: number } {
  const dot = fileName.lastIndexOf(".");
  const ext = dot >= 0 ? fileName.slice(dot).toLowerCase() : "";
  let raw: unknown[];
  if (ext === ".json") {
    const d: unknown = JSON.parse(text.charCodeAt(0) === 0xfeff ? text.slice(1) : text);
    raw = Array.isArray(d) ? d : [d];
  } else if (ext === ".csv") {
    const rows = parseCsv(text);
    const head = rows[0] ?? [];
    raw = rows.slice(1).map((r) => Object.fromEntries(head.map((h, i) => [h, i < r.length ? r[i] : null])));
  } else if (ext === ".xlsx" || ext === ".xlsm") {
    throw new Error("Excel files can't be read on the phone. Export the profiles as JSON from the desktop app.");
  } else {
    throw new Error(`Unsupported import format: ${ext || "(no extension)"} - use a .json or .csv file`);
  }
  const records: ProfileImportRecord[] = [];
  let invalid = 0;
  for (const r of raw) {
    if (!r || typeof r !== "object" || Array.isArray(r)) { invalid += 1; continue; }
    const rec = r as Record<string, unknown>;
    const extra: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(rec)) {
      const nk = normHeader(k);
      if (nk === "logo_data" || nk === "logo_name") extra[nk] = v;
    }
    const norm = normalizeRecord(rec);
    if (!Object.keys(norm).length) continue;
    const profile = profileFromRecord(norm);
    records.push({ profile: { ...profile, id: "", logo_path: "" }, logoPath: profile.logo_path,
      logoData: typeof extra.logo_data === "string" ? extra.logo_data : "",
      logoName: typeof extra.logo_name === "string" ? extra.logo_name : "" });
  }
  return { records, invalid };
}

/** The desktop's portable JSON: every field plus the logo embedded. */
export function buildProfilesJson(profiles: CompanyProfile[], logos: Record<string, { name: string; base64: string }>): string {
  return JSON.stringify(profiles.map((p) => {
    const row: Record<string, unknown> = { ...p };
    const logo = logos[p.id];
    if (logo) { row.logo_path = logo.name; row.logo_name = logo.name; row.logo_data = logo.base64; }
    else row.logo_path = "";
    return row;
  }), null, 2);
}
