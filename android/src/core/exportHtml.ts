/** Minutes as a branded HTML document for Android's print engine (PDF), in the
 *  company's design — the HTML/CSS counterpart of desktop export/pdf_export.py. */
import data from "./data/desktop.json";
import { normalizeStatus } from "./actionItems";
import { type Design, getDesign } from "./designs";
import { cleanText, parse, plain, runs } from "./mdBlocks";
import { type CompanyProfile, sanitizeProfile } from "./profiles";
import { hasArabic } from "./rtl";

export const DEFAULT_ACCENT = "#8B1E1E";
const TEXT = "#221D1D", MUTED = "#6C6269", BORDER = "#E4DEDC", PANEL = "#F7F4F3", GRID_HEAD = "#ECE9E8", GRID_LINE = "#9A9096";
const STATUS_COLORS = data.statusColors as Record<string, string>;
const TITLE_KEYS = new Set<string>(data.pdfTitleKeys);
const PEOPLE_KEYS = new Set<string>(data.pdfPeopleKeys);
const STATUS_HEADERS = new Set<string>(data.pdfStatusHeaders);
const PLACEHOLDERS = new Set<string>(data.pdfPlaceholders);
const COLOR_RE = /^#[0-9A-Fa-f]{3,8}$/;
const LOGO_RE = /^data:image\/(png|jpe?g|gif|bmp|webp);base64,[A-Za-z0-9+/=]+$/;
const SANS = `Roboto, "Noto Sans", "Noto Sans Arabic", "Noto Naskh Arabic", sans-serif`;
const SERIF = `"Noto Serif", "Noto Naskh Arabic", serif`;

export function safeColor(v: unknown, def = DEFAULT_ACCENT): string {
  const s = String(v ?? "").trim();
  return COLOR_RE.test(s) ? s : def;
}

export function escapeHtml(s: unknown): string {
  return cleanText(s).split("&").join("&amp;").split("<").join("&lt;").split(">").join("&gt;")
    .split('"').join("&quot;").split("'").join("&#39;");
}

const inline = (t: string) => runs(t).map(([s, b]) => (b ? `<strong>${escapeHtml(s)}</strong>` : escapeHtml(s))).join("");
const isPlaceholder = (v: string) => PLACEHOLDERS.has(plain(v).trim().toLowerCase());

// ---- colour ------------------------------------------------------------------
type RGB = [number, number, number];
function rgb(hex: string): RGB {
  let h = hex.slice(1);
  if (h.length === 3 || h.length === 4) h = h.slice(0, 3).split("").map((c) => c + c).join("");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
function luminance([r, g, b]: RGB): number {
  const f = (c: number) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
const contrastWithWhite = (c: RGB) => 1.05 / (luminance(c) + 0.05);
const hexOf = (c: RGB) => "#" + c.map((x) => Math.round(x).toString(16).padStart(2, "0")).join("");
/** The accent darkened until text in it reads on white (at least 4.5:1). */
function readable(accent: string): string {
  let c = rgb(accent);
  for (let i = 0; i < 20 && contrastWithWhite(c) < 4.5; i++) c = c.map((x) => x * 0.85) as RGB;
  return hexOf(c);
}
const tint = (accent: string, a: number) => { const [r, g, b] = rgb(accent); return `rgba(${r}, ${g}, ${b}, ${a})`; };

// ---- page margin boxes ----------------------------------------------------------
function cssString(s: string): string {
  return `"${s.split("\\").join("\\\\").split('"').join('\\"').split("\n").join("\\A ").split("<").join("\\3C ")}"`;
}
function pageNumberContent(fmt: string): string {
  return fmt.split(/(\{n\}|\{total\})/).filter((t) => t).map((t) =>
    t === "{n}" ? "counter(page)" : t === "{total}" ? "counter(pages)" : cssString(cleanText(t))).join(" ");
}
const box = (pos: string) => (pos.startsWith("header-") ? "@top-" : "@bottom-") + pos.split("-")[1];

// ---- blocks --------------------------------------------------------------------
function details(rows: Array<[string, string]>, d: Design): string {
  const v = (s: string) => (isPlaceholder(s) ? `<span class="muted">${inline(s)}</span>` : inline(s));
  if (d.details === "cards") {
    return `<div class="details cards">${rows.map(([k, s]) => `<div class="card"><div class="k">${inline(k)}</div><div class="v" dir="auto">${v(s)}</div></div>`).join("")}</div>`;
  }
  if (d.details === "inline") {
    return `<p class="details inline" dir="auto">${rows.map(([k, s]) => `<strong>${inline(k)}:</strong> ${v(s)}`).join(" &nbsp;·&nbsp; ")}</p>`;
  }
  return `<table class="details ${d.details === "grid" ? "grid" : "panel"}">${rows
    .map(([k, s]) => `<tr><th>${inline(k)}</th><td dir="auto">${v(s)}</td></tr>`).join("")}</table>`;
}

/** "A (Chair), B; C and D" -> ["A (Chair)", "B", "C", "D"]: commas inside
 *  brackets don't split (desktop pdf_export._split_names). */
export function splitNames(value: string): string[] {
  const parts: string[] = [];
  let buf = "";
  let depth = 0;
  for (const ch of value) {
    if ("([{".includes(ch)) depth += 1;
    else if (")]}".includes(ch)) depth = Math.max(depth - 1, 0);
    if (",;،؛\n".includes(ch) && depth === 0) { parts.push(buf); buf = ""; } else buf += ch;
  }
  parts.push(buf);
  const strip = (s: string) => s.replace(/^[ .]+|[ .]+$/g, "");
  const names = parts.map(strip).filter(Boolean);
  if (names.length > 1 && /\s+and\s+/.test(names[names.length - 1])) {
    names.push(...names.pop()!.split(/\s+and\s+/).map((n) => n.trim()).filter(Boolean));
  }
  return names;
}

function people(key: string, value: string): string {
  const names = isPlaceholder(value) ? [] : splitNames(plain(value));
  const body = names.length
    ? `<ul class="names">${names.map((n) => `<li dir="auto">${escapeHtml(n)}</li>`).join("\n")}</ul>`
    : `<p class="muted">${inline(value || "Not specified")}</p>`;
  return `<section class="people"><div class="k">${inline(key)}</div>${body}</section>`;
}

function table(headers: string[], rows: string[][], d: Design): string {
  const statusCol = headers.findIndex((h) => STATUS_HEADERS.has(plain(h).trim().toLowerCase()));
  const cell = (s: string, j: number) => {
    if (j === statusCol && d.status_color) {
      const color = STATUS_COLORS[normalizeStatus(plain(s))];
      if (color) return `<span class="dot" style="background:${color}"></span>${inline(s)}`;
    }
    return inline(s);
  };
  const head = headers.map((h) => `<th dir="auto">${inline(h)}</th>`).join("");
  const body = rows.map((r) => `<tr>${headers.map((_h, j) => `<td dir="auto">${cell(r[j] ?? "", j)}</td>`).join("")}</tr>`).join("");
  return `<table class="t t-${d.table}"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function signatures(arabic: boolean): string {
  const L: string[] = arabic ? data.signaturesAr : data.signaturesEn;
  const col = (title: string) => `<div class="sig"><div class="sig-title"><strong>${escapeHtml(title)}</strong></div>${L.slice(2)
    .map((label) => `<div class="sig-line"><span>${escapeHtml(label)}</span><span class="line"></span></div>`).join("")}</div>`;
  return `<section class="signatures">${col(L[0])}${col(L[1])}</section>`;
}

function letterhead(p: CompanyProfile, d: Design, accent: string, logoDataUri?: string | null): string {
  const logo = logoDataUri && LOGO_RE.test(logoDataUri)
    ? `<img class="logo" src="${logoDataUri}" style="width:${Number(p.logo_width_mm) || 35}mm;max-height:${d.logo_max_mm}mm" alt="">` : "";
  const contact = [p.address, p.phone, p.email, p.website].filter((x) => String(x ?? "").trim()).map(escapeHtml).join(" &nbsp;·&nbsp; ");
  const nameColor = d.name_accent ? readable(accent) : TEXT;
  const band = d.top_band_mm ? `<div class="band" style="height:${d.top_band_mm}mm"></div>` : "";
  return `<header class="letterhead lh-${d.letterhead} logo-${p.logo_position}">${band}<div class="lh-row">${logo}` +
    `<div class="lh-text"><div class="company" style="font-size:${d.name_max}pt;color:${nameColor}">${escapeHtml(p.name)}</div>` +
    (contact ? `<div class="contact">${contact}</div>` : "") + `</div></div></header>`;
}

function css(d: Design, accent: string, p: CompanyProfile | null, running: string): string {
  const ink = readable(accent);
  const headText = contrastWithWhite(rgb(accent)) >= 3 ? "#FFFFFF" : TEXT;
  const boxes = new Map<string, string[]>();
  const add = (b: string, c: string) => boxes.set(b, [...(boxes.get(b) ?? []), c]);
  if (p?.footer_text) add("@bottom-" + p.footer_alignment, cssString(cleanText(p.footer_text)));
  if (p?.show_page_numbers) add(box(p.page_number_position), pageNumberContent(p.page_number_format || "Page {n} of {total}"));
  const runningBox = ["@top-left", "@top-right"].find((b) => !boxes.has(b));
  const boxCss = [...boxes].map(([b, parts]) => `${b} { content: ${parts.join(' "   " ')}; font-size: 7.5pt; color: ${MUTED}; }`).join("\n  ");
  const runningCss = running && runningBox ? `${runningBox} { content: ${cssString(running)}; font-size: 7.5pt; color: ${MUTED}; }` : "";
  const firstCss = running && runningBox ? `@page :first { ${runningBox} { content: none; } }` : "";
  const g = d.gap;
  return `
@page { size: A4; margin: 16mm 16mm 18mm 16mm;
  ${boxCss}
  ${runningCss} }
${firstCss}
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; color: ${TEXT}; font-family: ${d.font === "serif" ? SERIF : SANS}; font-size: ${d.body}pt; line-height: ${d.lead}pt; }
.letterhead { margin-bottom: ${10 * g}pt; padding-bottom: 6pt; }
.lh-rule { border-bottom: 2pt solid ${accent}; } .lh-hairline { border-bottom: 0.5pt solid ${BORDER}; }
.lh-double { border-bottom: 3pt double ${accent}; }
.band { background: ${accent}; margin-bottom: 6pt; }
.lh-row { display: flex; align-items: center; gap: 10pt; }
.logo-right .lh-row { flex-direction: row-reverse; text-align: end; }
.logo-center .lh-row { flex-direction: column; text-align: center; }
.company { font-weight: 700; line-height: 1.2; } .contact { color: ${MUTED}; font-size: 8pt; margin-top: 2pt; }
.eyebrow { color: ${MUTED}; font-size: 8pt; letter-spacing: .08em; margin: 0; }
h1.title { font-size: ${d.title_size}pt; line-height: 1.25; margin: 2pt 0 ${8 * g}pt; text-align: ${d.title_align}; color: ${TEXT}; }
.details { margin: 0 0 ${8 * g}pt; width: 100%; }
table.details { border-collapse: collapse; }
.details th { text-align: start; color: ${ink}; font-weight: 600; width: 28%; padding: 3pt 6pt; vertical-align: top; }
.details td { padding: 3pt 6pt; }
.details.panel { background: ${PANEL}; border-radius: 4pt; }
.details.grid th, .details.grid td { border: 0.5pt solid ${GRID_LINE}; } .details.grid th { background: ${GRID_HEAD}; color: ${TEXT}; }
.details.cards { display: flex; flex-wrap: wrap; gap: 6pt; }
.card { border: 0.5pt solid ${BORDER}; border-top: 2pt solid ${accent}; border-radius: 3pt; padding: 4pt 6pt; min-width: 30%; flex: 1; }
.card .k { color: ${ink}; font-size: 7.5pt; text-transform: uppercase; letter-spacing: .05em; }
.people { margin: 0 0 ${8 * g}pt; } .people .k { color: ${ink}; font-weight: 600; margin-bottom: 2pt; }
.names { list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 2pt 10pt; }
h2.sec { font-size: ${d.h2_size}pt; line-height: 1.3; margin: ${13 * g}pt 0 ${6 * g}pt; break-after: avoid; page-break-after: avoid; }
.sec-rule { color: ${ink}; border-bottom: 0.7pt solid ${BORDER}; padding-bottom: 2pt; }
.sec-bar { color: ${ink}; background: ${tint(accent, 0.1)}; border-inline-start: 3pt solid ${accent}; padding: 3pt 6pt; }
.sec-numbered { color: ${TEXT}; text-transform: uppercase; letter-spacing: .04em; }
.sec-plain { color: ${TEXT}; border-bottom: 0.5pt solid ${BORDER}; }
h3 { font-size: ${d.h2_size - 1.5}pt; margin: ${9 * g}pt 0 ${4 * g}pt; break-after: avoid; page-break-after: avoid; }
p { margin: 0 0 ${5 * g}pt; } ul, ol { margin: 0 0 ${5 * g}pt; padding-inline-start: 16pt; } li { break-inside: avoid; }
.muted { color: ${MUTED}; font-style: italic; }
table.t { width: 100%; border-collapse: collapse; font-size: ${d.cell}pt; line-height: 1.3; margin: 3pt 0 8pt; }
table.t thead { display: table-header-group; } table.t tr { break-inside: avoid; page-break-inside: avoid; }
table.t th, table.t td { padding: 3pt 5pt; text-align: start; vertical-align: top; }
.t-fill th { background: ${accent}; color: ${headText}; } .t-fill td { border-bottom: 0.5pt solid ${BORDER}; }
.t-tint th { background: ${tint(accent, 0.12)}; color: ${ink}; } .t-tint td { border-bottom: 0.5pt solid ${BORDER}; }
.t-grid th, .t-grid td { border: 0.5pt solid ${GRID_LINE}; } .t-grid th { background: ${GRID_HEAD}; }
.t-lines th { border-bottom: 1pt solid ${TEXT}; } .t-lines td { border-bottom: 0.3pt solid ${BORDER}; }
.dot { display: inline-block; width: 6pt; height: 6pt; border-radius: 50%; margin-inline-end: 4pt; vertical-align: middle; }
.signatures { display: flex; gap: 10mm; margin-top: 9mm; break-inside: avoid; page-break-inside: avoid; }
.sig { flex: 1; } .sig-line { display: flex; align-items: flex-end; gap: 6pt; height: 9mm; }
.sig-line .line { flex: 1; border-bottom: 0.6pt solid ${TEXT}; }`;
}

export interface HtmlOptions { profile?: CompanyProfile | null; design?: string | null; logoDataUri?: string | null; }

export function renderMinutesHtml(md: string, opts: HtmlOptions = {}): string {
  const profile = opts.profile ? sanitizeProfile(opts.profile) : null;   // choices reach CSS and class names
  const d = getDesign(opts.design ?? profile?.pdf_design);
  const accent = safeColor(profile?.accent_color);
  const blocks = parse(md ?? "");

  let i = 0;
  let h1 = "";
  if (blocks.length && blocks[0].kind === "h1") { h1 = blocks[0].text; i = 1; }
  const det: Array<[string, string]> = [];
  while (i < blocks.length && blocks[i].kind === "kv") { det.push([blocks[i].key, blocks[i].text]); i += 1; }
  let title = "";
  let titleAt = -1;
  det.some(([k, v], n) => {
    if (TITLE_KEYS.has(plain(k).trim().toLowerCase()) && !isPlaceholder(v)) { title = v; titleAt = n; return true; }
    return false;
  });
  const mirror = hasArabic(h1 || title || (det[0]?.[0] ?? ""));

  const out: string[] = [];
  if (title) {
    if (h1) out.push(`<p class="eyebrow" dir="auto">${escapeHtml(plain(h1).toUpperCase())}</p>`);
    out.push(`<h1 class="title" dir="auto">${inline(title)}</h1>`);
  } else if (h1) {
    out.push(`<h1 class="title" dir="auto">${inline(h1)}</h1>`);
  }
  const panel = det.filter(([k], n) => n !== titleAt && !PEOPLE_KEYS.has(plain(k).trim().toLowerCase()));
  const ppl = det.filter(([k]) => PEOPLE_KEYS.has(plain(k).trim().toLowerCase()));
  if (panel.length) out.push(details(panel, d));
  for (const [k, v] of ppl) out.push(people(k, v));

  let counter = 0;
  for (const b of blocks.slice(i)) {
    if (b.kind === "h1" || b.kind === "h2") {
      counter += 1;
      out.push(d.heading === "numbered"
        ? `<h2 class="sec sec-numbered" dir="auto">${escapeHtml(`${counter}. ${plain(b.text).toUpperCase()}`)}</h2>`
        : `<h2 class="sec sec-${d.heading}" dir="auto">${inline(b.text)}</h2>`);
    } else if (b.kind === "h3") out.push(`<h3 dir="auto">${inline(b.text)}</h3>`);
    else if (b.kind === "bullet") out.push(`<ul>${b.items.map((it) => `<li dir="auto">${inline(it)}</li>`).join("")}</ul>`);
    else if (b.kind === "olist") out.push(`<ol${b.start !== 1 ? ` start="${b.start}"` : ""}>${b.items.map((it) => `<li dir="auto">${inline(it)}</li>`).join("")}</ol>`);
    else if (b.kind === "kv") out.push(`<p dir="auto"><strong>${inline(b.key)}:</strong> ${inline(b.text)}</p>`);
    else if (b.kind === "para") out.push(`<p dir="auto"${isPlaceholder(b.text) ? ' class="muted"' : ""}>${inline(b.text)}</p>`);
    else if (b.kind === "table") out.push(table(b.headers, b.rows, d));
  }
  if (!out.length) out.push("<p>No minutes content.</p>");
  else if (d.signatures) out.push(signatures(mirror));

  const docTitle = plain(title || h1).trim() || "Minutes of Meeting";
  const company = profile ? cleanText(profile.name).trim() : "";
  const running = profile ? [company, plain(title || h1).trim()].filter(Boolean).join("  ·  ") : "";
  return `<!doctype html><html lang="${mirror ? "ar" : "en"}" dir="${mirror ? "rtl" : "ltr"}"><head><meta charset="utf-8">` +
    `<title>${escapeHtml(docTitle)}</title><style>${css(d, accent, profile, running)}</style></head>` +
    `<body class="design-${d.key}">${profile ? letterhead(profile, d, accent, opts.logoDataUri) : ""}<main>${out.join("\n")}</main></body></html>`;
}

/** Body fragment for the in-app preview (no page furniture). */
export function renderPreviewHtml(md: string, accent?: string | null): string {
  const a = safeColor(accent);
  return parse(md ?? "").map((b) => {
    if (b.kind === "h1" || b.kind === "h2" || b.kind === "h3") {
      const lvl = b.kind === "h1" ? 1 : b.kind === "h2" ? 2 : Math.min(Math.max(b.level, 3), 6);
      return `<h${lvl} dir="auto" style="color:${a}">${inline(b.text)}</h${lvl}>`;
    }
    if (b.kind === "kv") return `<p dir="auto"><strong>${inline(b.key)}:</strong> ${inline(b.text)}</p>`;
    if (b.kind === "bullet") return `<ul>${b.items.map((it) => `<li dir="auto">${inline(it)}</li>`).join("")}</ul>`;
    if (b.kind === "olist") return `<ol${b.start !== 1 ? ` start="${b.start}"` : ""}>${b.items.map((it) => `<li dir="auto">${inline(it)}</li>`).join("")}</ol>`;
    if (b.kind === "table") {
      const head = b.headers.map((h) => `<th dir="auto">${inline(h)}</th>`).join("");
      const rows = b.rows.map((r) => `<tr>${b.headers.map((_h, j) => `<td dir="auto">${inline(r[j] ?? "")}</td>`).join("")}</tr>`).join("");
      return `<table><thead style="background:${a};color:#fff"><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
    }
    return `<p dir="auto">${inline(b.text)}</p>`;
  }).join("\n");
}
