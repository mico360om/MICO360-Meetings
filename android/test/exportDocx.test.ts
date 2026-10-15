import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { buildDocxBase64 } from "../src/core/exportDocx";
import { buildMd, buildTxt } from "../src/core/exportText";
import { DEFAULT_PROFILE } from "../src/core/profiles";

const MD = "# Meeting Minutes\n**Attendees:** Ali\n## Notes\n- one **bold**\n1. first\n| Task | Owner |\n|---|---|\n| Send | Ali |\n\nمحضر الاجتماع النهائي";
const profile = { ...DEFAULT_PROFILE, id: "p", name: "Acme", phone: "123", footer_text: "Confidential" };

describe("text and markdown", () => {
  it("txt: header, no Markdown marks, footer", () => {
    const t = buildTxt(MD, profile);
    expect(t.startsWith("Acme\n123\n" + "=".repeat(60) + "\n\n")).toBe(true);
    expect(t).not.toContain("**");
    expect(t).not.toMatch(/^#/m);
    expect(t.trimEnd().endsWith("Confidential")).toBe(true);
  });
  it("md: profile header and footer around the minutes", () => {
    expect(buildMd("x", profile)).toBe("**Acme**  \n123\n\n---\n\nx\n\n---\nConfidential\n");
  });
});

describe("word", () => {
  it("a real .docx with the content, the table, RTL Arabic, header and page numbers", async () => {
    const zip = await JSZip.loadAsync(await buildDocxBase64(MD, profile), { base64: true });
    const doc = await zip.file("word/document.xml")!.async("string");
    expect(doc).toContain("Meeting Minutes");
    expect(doc).toContain("<w:tbl>");
    expect(doc).toContain("Send");
    expect(doc).toMatch(/<w:bidi\/>/);
    const parts = Object.keys(zip.files).join(" ");
    expect(parts).toMatch(/word\/header\d*\.xml/);
    const footers = await Promise.all(Object.keys(zip.files).filter((f) => /word\/footer\d*\.xml/.test(f)).map((f) => zip.file(f)!.async("string")));
    expect(footers.join("")).toContain("NUMPAGES");
  });
});
