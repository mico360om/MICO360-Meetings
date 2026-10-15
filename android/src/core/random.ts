/** Random bytes for ids. Hermes has no WebCrypto, so the app installs a secure
 *  source at start-up (expo-crypto); Node and tests use globalThis.crypto. */
type Source = (n: number) => Uint8Array;

let installed: Source | null = null;
let counter = 0;

export function setRandomSource(source: Source | null): void {
  installed = source;
}

/** `n` random bytes. Without any secure source the bytes start with the time
 *  and a per-process counter, so two calls never repeat even if Math.random does. */
export function randomBytes(n: number): Uint8Array {
  if (installed) return installed(n).slice(0, n);
  const out = new Uint8Array(n);
  const c = (globalThis as { crypto?: { getRandomValues?: (a: Uint8Array) => Uint8Array } }).crypto;
  if (c?.getRandomValues) { c.getRandomValues(out); return out; }
  for (let i = 0; i < n; i++) out[i] = Math.floor(Math.random() * 256);
  let t = Date.now();
  counter = (counter + 1) % 65536;
  const stamp = [counter & 255, counter >> 8];
  for (let i = 0; i < 6; i++) { stamp.push(t % 256); t = Math.floor(t / 256); }
  stamp.forEach((b, i) => { if (i < n) out[i] ^= b; });
  return out;
}

export function randomHex(bytes: number): string {
  return Array.from(randomBytes(bytes), (b) => b.toString(16).padStart(2, "0")).join("");
}
