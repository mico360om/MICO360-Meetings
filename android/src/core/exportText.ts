/** Plain-text and Markdown exports (port of desktop txt_export.py / export_md). */
import { cleanText } from "./mdBlocks";
import type { CompanyProfile } from "./profiles";

function txtHeader(p?: CompanyProfile | null): string {
  if (!p) return "";
  const lines = [p.name, ...[p.address, p.phone, p.email, p.website].filter((x) => x)];
  lines.push("=".repeat(60));
  return lines.join("\n") + "\n\n";
}

export function buildTxt(md: string, profile?: CompanyProfile | null): string {
  let body = cleanText(md ?? "").split("**").join("");
  body = body.replace(/^#+\s*/gm, "");
  let text = txtHeader(profile) + body.trim() + "\n";
  if (profile?.footer_text) text += "\n" + "-".repeat(60) + "\n" + String(profile.footer_text) + "\n";
  return cleanText(text);
}

export function buildMd(md: string, profile?: CompanyProfile | null): string {
  let head = "";
  if (profile) {
    const bits = [profile.address, profile.phone, profile.email, profile.website].filter((x) => x).join(" · ");
    head = `**${profile.name}**` + (bits ? `  \n${bits}` : "") + "\n\n---\n\n";
  }
  const foot = profile?.footer_text ? `\n\n---\n${profile.footer_text}\n` : "";
  return cleanText(head + (md ?? "").trim() + foot);
}
