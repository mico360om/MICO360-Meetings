/** Word (.docx) export with letterhead, logo, footer and page numbers
 *  (port of desktop export/docx_export.py, using the `docx` library). */
import {
  AlignmentType, Document, Footer, Header, HeadingLevel, ImageRun, type IParagraphOptions, Packer, PageNumber,
  Paragraph, ShadingType, Table, TableCell, TableRow, TextRun, WidthType,
} from "docx";
import { safeColor } from "./exportHtml";
import { type Block, cleanText, parse, runs } from "./mdBlocks";
import type { CompanyProfile } from "./profiles";
import { hasArabic } from "./rtl";

export interface DocxLogo { base64: string; type: "png" | "jpg" | "gif" | "bmp"; widthPx: number; heightPx: number; }

type Align = (typeof AlignmentType)[keyof typeof AlignmentType];
const align = (s: string): Align =>
  s === "center" ? AlignmentType.CENTER : s === "right" ? AlignmentType.RIGHT : AlignmentType.LEFT;

function hex6(c: string): string {
  let h = safeColor(c).slice(1);
  if (h.length <= 4) h = h.slice(0, 3).split("").map((x) => x + x).join("");
  return h.slice(0, 6).toUpperCase();
}

interface RunStyle { bold?: boolean; color?: string; size?: number; }

function textRuns(text: string, style: RunStyle, rtl: boolean): TextRun[] {
  return runs(cleanText(text)).filter(([t]) => t).map(([t, b]) =>
    new TextRun({ text: t, bold: b || style.bold, color: style.color, size: style.size, rightToLeft: rtl || undefined }));
}

/** A paragraph of Markdown-inline text; Arabic paragraphs are right-to-left. */
function para(text: string, opts: Omit<IParagraphOptions, "children"> = {}, style: RunStyle = {}): Paragraph {
  const rtl = hasArabic(text);
  return new Paragraph({ ...opts, ...(rtl ? { bidirectional: true, alignment: AlignmentType.RIGHT } : {}),
    children: textRuns(text, style, rtl) });
}

function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function header(p: CompanyProfile, logo?: DocxLogo | null): Header {
  const children: Paragraph[] = [];
  if (logo) {
    const w = (Number(p.logo_width_mm) || 35) * 3.78;              // mm -> px at 96 dpi
    const h = logo.widthPx ? (w * logo.heightPx) / logo.widthPx : w / 3;
    children.push(new Paragraph({ alignment: align(p.logo_position), children: [
      new ImageRun({ type: logo.type, data: b64ToBytes(logo.base64), transformation: { width: Math.round(w), height: Math.round(h) } })] }));
  }
  const contact = [p.address, p.phone, p.email, p.website].filter((x) => x).map(cleanText).join("  •  ");
  children.push(new Paragraph({ alignment: align(p.logo_position), children: [
    new TextRun({ text: cleanText(p.name), bold: true, size: 26, color: hex6(p.accent_color) }),
    ...(contact ? [new TextRun({ text: contact, size: 16, color: "666666", break: 1 })] : []),
  ] }));
  return new Header({ children });
}

function footer(p: CompanyProfile): Footer {
  const children: Paragraph[] = [];
  if (p.footer_text) {
    children.push(new Paragraph({ alignment: align(p.footer_alignment),
      children: [new TextRun({ text: cleanText(p.footer_text), size: 16, color: "888888" })] }));
  }
  if (p.show_page_numbers) {
    const pos = p.page_number_position;
    const parts = (p.page_number_format || "Page {n} of {total}").split(/(\{n\}|\{total\})/).filter((t) => t)
      .map((t) => (t === "{n}" ? PageNumber.CURRENT : t === "{total}" ? PageNumber.TOTAL_PAGES : cleanText(t)));
    children.push(new Paragraph({
      alignment: pos.endsWith("right") ? AlignmentType.RIGHT : pos.endsWith("left") ? AlignmentType.LEFT : AlignmentType.CENTER,
      children: [new TextRun({ children: parts, size: 16 })] }));
  }
  return new Footer({ children });
}

function tableOf(b: Block, accent: string): Table {
  const cols = Math.max(b.headers.length, 1);
  const cell = (t: string, head: boolean) => new TableCell({
    shading: head ? { type: ShadingType.CLEAR, fill: accent, color: "auto" } : undefined,
    children: [para(t, {}, head ? { bold: true, color: "FFFFFF" } : {})] });
  return new Table({ width: { size: 100, type: WidthType.PERCENTAGE }, rows: [
    new TableRow({ tableHeader: true, children: Array.from({ length: cols }, (_x, j) => cell(b.headers[j] ?? "", true)) }),
    ...b.rows.map((r) => new TableRow({ children: Array.from({ length: cols }, (_x, j) => cell(r[j] ?? "", false)) })),
  ] });
}

const SUB_HEADINGS = [HeadingLevel.HEADING_2, HeadingLevel.HEADING_3, HeadingLevel.HEADING_4, HeadingLevel.HEADING_5];

export async function buildDocxBase64(md: string, profile?: CompanyProfile | null, logo?: DocxLogo | null): Promise<string> {
  const accent = hex6(profile?.accent_color ?? "#8B1E1E");
  const body: Array<Paragraph | Table> = [];
  for (const b of parse(md ?? "")) {
    if (b.kind === "h1") body.push(para(b.text, { heading: HeadingLevel.TITLE }, { color: accent }));
    else if (b.kind === "h2") body.push(para(b.text, { heading: HeadingLevel.HEADING_1 }));
    else if (b.kind === "h3") body.push(para(b.text, { heading: SUB_HEADINGS[Math.min(Math.max(b.level - 3, 0), 3)] }));
    else if (b.kind === "kv") body.push(para(`**${b.key}:** ${b.text}`));
    else if (b.kind === "bullet") for (const it of b.items) body.push(para(it, { bullet: { level: 0 } }));
    else if (b.kind === "olist") b.items.forEach((it, k) => body.push(para(`${b.start + k}.\xa0${it}`, { indent: { left: 340, hanging: 340 } })));
    else if (b.kind === "table") body.push(tableOf(b, accent));
    else body.push(para(b.text));
  }
  const doc = new Document({
    styles: { default: { document: { run: { font: "Calibri", size: 22 } } } },
    sections: [{
      headers: profile ? { default: header(profile, logo) } : undefined,
      footers: profile ? { default: footer(profile) } : undefined,
      children: body.length ? body : [new Paragraph("No minutes content.")],
    }],
  });
  return Packer.toBase64String(doc);
}
