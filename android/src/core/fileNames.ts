const RESERVED = new Set(["CON", "PRN", "AUX", "NUL", ...Array.from({ length: 9 }, (_x, i) => `COM${i + 1}`),
  ...Array.from({ length: 9 }, (_x, i) => `LPT${i + 1}`)]);

/** A file name (no extension) safe on Android, Windows and in e-mail clients
 *  (desktop export/service.py safe_filename). */
export function safeFilename(name: string, fallback = "Meeting-Minutes", maxLen = 100): string {
  let s = (name ?? "").replace(/[<>:"/\\|?*\x00-\x1f]/g, " ");
  s = s.replace(/\s+/g, " ").trim().replace(/[. ]+$/, "");
  s = s.slice(0, maxLen).replace(/[. ]+$/, "");
  if (RESERVED.has(s.split(".")[0].toUpperCase())) s = "_" + s;
  return s || fallback;
}
