import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? files(p) : p.endsWith(".ts") ? [p] : [];
  });
}

it("src/core imports no React Native, Expo or native module", () => {
  for (const f of files(join(__dirname, "..", "src", "core"))) {
    const src = readFileSync(f, "utf8");
    expect(src, f).not.toMatch(/from\s+["'](react-native|expo[^"']*|react)["']/);
    expect(src, f).not.toMatch(/\\p\{/);
  }
});

it("src/core holds no invisible separators or non-characters as literal text (write them as escapes)", () => {
  const banned = new RegExp("[" + String.fromCharCode(0x2028, 0x2029, 0xfeff, 0xfffe, 0xffff, 0) + "]");
  for (const f of files(join(__dirname, "..", "src", "core"))) expect(readFileSync(f, "utf8"), f).not.toMatch(banned);
});
