import { describe, expect, it } from "vitest";
import fx from "./fixtures/profiles.json";
import { parseCsv } from "../src/core/csv";
import * as P from "../src/core/profiles";

const BOM = String.fromCharCode(0xfeff);

describe("parity with desktop profiles.py", () => {
  it("coerce", () => { for (const c of fx.coerce) expect([c.key, c.value, P.coerce(c.key, c.value)]).toEqual([c.key, c.value, c.output]); });
  it("normHeader", () => { for (const c of fx.normHeader) expect(P.normHeader(c.input)).toBe(c.output); });
  it("normalizeRecord", () => {
    for (const c of fx.normalize) expect(P.normalizeRecord(c.input as Record<string, unknown>)).toEqual(c.output);
  });
});

describe("import", () => {
  it("reads a desktop JSON export with an embedded logo", () => {
    const json = JSON.stringify([{ id: "abc", name: "Acme", phone: 96824123456, accent_color: "red;}</style>",
      logo_path: "C:/x/logo.png", logo_name: "logo.png", logo_data: "iVBORw0KGgo=" }]);
    const { records, invalid } = P.parseProfileFile("profiles.json", json);
    expect(invalid).toBe(0);
    expect(records[0].profile).toMatchObject({ id: "", name: "Acme", phone: "96824123456", accent_color: "#8B1E1E", pdf_design: "classic" });
    expect(records[0].logoData).toBe("iVBORw0KGgo=");
    expect(records[0].logoPath).toBe("C:/x/logo.png");
  });
  it("reads CSV with friendly headers and skips blank rows", () => {
    const csv = BOM + "Company Name,Tel,Design\r\nAcme,123,Formal\r\n,,\r\n\"Beta, Inc\",\"4\"\"5\",modern\r\n";
    const { records } = P.parseProfileFile("p.csv", csv);
    expect(records.map((r) => [r.profile.name, r.profile.phone, r.profile.pdf_design])).toEqual([
      ["Acme", "123", "formal"], ["Beta, Inc", '4"5', "modern"]]);
  });
  it("refuses Excel with a way forward", () => {
    expect(() => P.parseProfileFile("p.xlsx", "")).toThrow(/Export the profiles as JSON from the desktop app/);
  });
  it("counts non-objects as invalid", () => {
    expect(P.parseProfileFile("p.json", "[1, null, {\"name\": \"A\"}]").invalid).toBe(2);
  });
});

describe("names and identity", () => {
  it("unique and copy names", () => {
    expect(P.uniqueProfileName("Acme", ["acme"])).toBe("Acme (2)");
    expect(P.copyProfileName("Acme", ["Acme (copy)"])).toBe("Acme (copy 2)");
  });
  it("content key ignores id and logo location but not the logo bytes", () => {
    const a = { ...P.DEFAULT_PROFILE, id: "1", name: "A", logo_path: "x" };
    const b = { ...a, id: "2", logo_path: "y" };
    expect(P.contentKey(a, "d1")).toBe(P.contentKey(b, "d1"));
    expect(P.contentKey(a, "d1")).not.toBe(P.contentKey(b, "d2"));
  });
  it("export embeds logos so the desktop can import them", () => {
    const out = JSON.parse(P.buildProfilesJson([{ ...P.DEFAULT_PROFILE, id: "1", name: "A", logo_path: "file:///l.png" }],
      { "1": { name: "l.png", base64: "AAAA" } }));
    expect(out[0]).toMatchObject({ name: "A", logo_name: "l.png", logo_data: "AAAA" });
  });
  it("a saved profile never keeps a blank name", () => {
    expect(P.sanitizeProfile({ ...P.DEFAULT_PROFILE, name: "  " }).name).toBe("Company");
  });
});

it("parseCsv handles quotes, CRLF and embedded newlines", () => {
  expect(parseCsv('a,"b\r\nc",d\r\n1,2,3')).toEqual([["a", "b\r\nc", "d"], ["1", "2", "3"]]);
});
