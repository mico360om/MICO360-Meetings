/** Light obfuscation for the build-time Cloud key: XOR with a per-build salt.
 *  Deters casual extraction from the APK only (spec §5) — never logged. */
import { randomBytes } from "./random";

export interface KeyBox { s: string; d: string; }

const toHex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
function fromHex(h: string): Uint8Array | null {
  if (!/^(?:[0-9a-f]{2})*$/.test(h)) return null;
  return Uint8Array.from(h.match(/../g) ?? [], (x) => parseInt(x, 16));
}

export function hideKey(key: string, salt: Uint8Array = randomBytes(32)): KeyBox {
  const bytes = new TextEncoder().encode(key);
  const out = bytes.map((b, i) => b ^ salt[i % salt.length]);
  return { s: toHex(salt), d: toHex(out) };
}

export function revealKey(box: KeyBox | null | undefined): string {
  if (!box || typeof box.s !== "string" || typeof box.d !== "string") return "";
  const salt = fromHex(box.s);
  const data = fromHex(box.d);
  if (!salt || !data || !salt.length) return "";
  return new TextDecoder().decode(data.map((b, i) => b ^ salt[i % salt.length]));
}
