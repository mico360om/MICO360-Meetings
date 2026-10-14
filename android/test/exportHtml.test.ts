import { describe, expect, it } from "vitest";
import { DEFAULT_PROFILE, type CompanyProfile } from "../src/core/profiles";
import { DESIGNS, getDesign } from "../src/core/designs";
import { renderMinutesHtml, renderPreviewHtml, safeColor } from "../src/core/exportHtml";

const MD = [
  "# Meeting Minutes", "**Meeting Title:** Budget <review>", "**Date and Time:** 5 Oct 2026", "**Attendees:** Ali, Sara; Omar",
  "", "## Decisions Made", "- Approve **plan**", "", "## Action Items",
  "| Task | Responsible Person | Deadline | Status |", "| --- | --- | --- | --- |",
  "| Send report | Ali | 10 Oct | Done |", "| Review | Sara | Not specified | Pending |",
].join("\n");
const profile: CompanyProfile = { ...DEFAULT_PROFILE, id: "p", name: "Acme & Co", address: "Muscat",
  footer_text: "Confidential", page_number_format: 'Page {n} of {total} "x"', accent_color: "#1F5FA8" };

describe("branded HTML for printing", () => {
  it("letterhead, title, details, attendees, sections and the action table", () => {
    const html = renderMinutesHtml(MD, { profile });
    expect(html).toContain("Acme &amp; Co");
    expect(html).toContain("Budget &lt;review&gt;");
    expect(html).toContain('class="details');
    expect(html).toMatch(/<li[^>]*>Ali<\/li>\s*<li[^>]*>Sara<\/li>\s*<li[^>]*>Omar<\/li>/);
    expect(html).toContain("<strong>plan</strong>");
    expect(html).toContain("<thead>");
    expect(html).toContain('class="dot"');
  });
  it("page numbers and footer in the page margins, quotes escaped", () => {
    const html = renderMinutesHtml(MD, { profile });
    expect(html).toContain('"Page " counter(page) " of " counter(pages) " \\"x\\""');
    expect(html).toContain('"Confidential"');
    expect(html).toContain("@bottom-right");
  });
  it("each design has its own look; only Formal signs off", () => {
    const looks = DESIGNS.map((d) => renderMinutesHtml(MD, { profile, design: d.key }));
    expect(new Set(looks.map((h) => /class="design-(\w+)/.exec(h)![1]))).toEqual(new Set(["classic", "modern", "formal", "compact"]));
    expect(looks.filter((h) => h.includes('class="signatures"'))).toHaveLength(1);
    expect(getDesign("nope").key).toBe("classic");
  });
  it("the company's own design is used when none is given", () => {
    expect(renderMinutesHtml(MD, { profile: { ...profile, pdf_design: "compact" } })).toContain('class="design-compact');
  });
  it("Arabic minutes are right-to-left", () => {
    expect(renderMinutesHtml("# محضر الاجتماع\n**عنوان الاجتماع:** الميزانية", { profile })).toContain('dir="rtl"');
  });
  it("hostile values are neutralised", () => {
    const evil = { ...profile, accent_color: "red;}</style><script>alert(1)</script>", name: "<script>x</script>" };
    const html = renderMinutesHtml("<img src=x onerror=alert(1)>", { profile: evil, logoDataUri: "javascript:alert(1)" });
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img src=x");
    expect(html).not.toContain("javascript:");
    expect(safeColor("#abc")).toBe("#abc");
    expect(safeColor("blue")).toBe("#8B1E1E");
  });
  it("a logo is embedded only as an image data URI", () => {
    expect(renderMinutesHtml(MD, { profile, logoDataUri: "data:image/png;base64,iVBORw0KGgo=" })).toContain('<img class="logo" src="data:image/png;base64,iVBORw0KGgo="');
  });
  it("no profile: plain minutes still render; nothing at all says so", () => {
    expect(renderMinutesHtml(MD)).toContain("Budget &lt;review&gt;");
    expect(renderMinutesHtml("")).toContain("No minutes content.");
  });
  it("preview fragment for the in-app viewer", () => {
    expect(renderPreviewHtml("## Hi\n- a")).toContain('<h2 dir="auto"');
  });
});
