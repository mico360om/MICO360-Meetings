// Right-to-left scripts: Arabic (+ Supplement, Extended-A, presentation forms
// A/B) and Hebrew (+ presentation forms), as desktop export/rtl.py.
const RANGES: Array<[number, number]> = [
  [0x0600, 0x06ff], [0x0750, 0x077f], [0x08a0, 0x08ff],
  [0xfb50, 0xfdff], [0xfe70, 0xfeff], [0x0590, 0x05ff], [0xfb1d, 0xfb4f],
];

export function hasArabic(text: string): boolean {
  for (const ch of text ?? "") {
    const cp = ch.codePointAt(0)!;
    for (const [lo, hi] of RANGES) if (cp >= lo && cp <= hi) return true;
  }
  return false;
}
