import { type ActionItem, buildActionItemsCsv } from "../core/actionItems";
import { buildDocxBase64, type DocxLogo } from "../core/exportDocx";
import { renderMinutesHtml } from "../core/exportHtml";
import { buildMd, buildTxt } from "../core/exportText";
import { safeFilename } from "../core/fileNames";
import { buildIcs, datedItems } from "../core/ics";
import type { CompanyProfile } from "../core/profiles";
import type { Meeting } from "../data/meetings";

export type ExportFormat = "pdf" | "docx" | "txt" | "md";
export const MIME: Record<ExportFormat | "csv" | "ics", string> = {
  pdf: "application/pdf", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  txt: "text/plain", md: "text/markdown", csv: "text/csv", ics: "text/calendar",
};

export interface LogoData extends DocxLogo { dataUri: string; }
export interface ExportDeps {
  /** Print HTML to an A4 PDF; returns its temporary URI. */
  pdfFromHtml(html: string): Promise<string>;
  writeText(name: string, text: string): Promise<string>;
  writeBase64(name: string, base64: string): Promise<string>;
  moveTo(uri: string, name: string): Promise<string>;
  logo(profile: CompanyProfile): Promise<LogoData | null>;
}

export async function exportMinutes(meeting: Meeting, profile: CompanyProfile | null, format: ExportFormat,
  deps: ExportDeps, design?: string | null): Promise<{ uri: string; mime: string; name: string }> {
  if (!meeting.minutes.trim()) throw new Error("This meeting has no minutes yet — generate or write them first.");
  const name = `${safeFilename(meeting.title)}.${format}`;
  const logo = profile ? await deps.logo(profile) : null;
  let uri: string;
  if (format === "pdf") {
    const html = renderMinutesHtml(meeting.minutes, { profile, design, logoDataUri: logo?.dataUri });
    uri = await deps.moveTo(await deps.pdfFromHtml(html), name);
  } else if (format === "docx") {
    uri = await deps.writeBase64(name, await buildDocxBase64(meeting.minutes, profile, logo));
  } else {
    uri = await deps.writeText(name, format === "txt" ? buildTxt(meeting.minutes, profile) : buildMd(meeting.minutes, profile));
  }
  return { uri, mime: MIME[format], name };
}

export async function exportActionItems(items: ActionItem[], kind: "csv" | "ics", deps: ExportDeps):
  Promise<{ uri: string; mime: string; name: string; count: number }> {
  const name = `Action-Items.${kind}`;
  if (kind === "csv") return { uri: await deps.writeText(name, buildActionItemsCsv(items)), mime: MIME.csv, name, count: items.length };
  const dated = datedItems(items);
  if (!dated.length) throw new Error("None of these action items has a date the calendar can use.");
  return { uri: await deps.writeText(name, buildIcs(dated)), mime: MIME.ics, name, count: dated.length };
}
