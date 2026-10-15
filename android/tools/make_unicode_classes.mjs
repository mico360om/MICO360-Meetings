// Build explicit character ranges for Python's \w (letters + numbers + "_") and
// for letters (+ non-decimal numbers), so src/core never needs \p{...} (Hermes).
//   node android/tools/make_unicode_classes.mjs
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

function ranges(test) {
  const out = [];
  let start = -1;
  for (let cp = 0; cp <= 0x110000; cp++) {
    const ok = cp < 0x110000 && !(cp >= 0xd800 && cp <= 0xdfff) && test(String.fromCodePoint(cp));
    if (ok && start < 0) start = cp;
    if (!ok && start >= 0) { out.push([start, cp - 1]); start = -1; }
  }
  return out;
}
const word = ranges((c) => /[\p{L}\p{N}_]/u.test(c));
const letter = ranges((c) => /[\p{L}\p{Nl}\p{No}]/u.test(c));
const url = new URL("../src/core/data/unicode.json", import.meta.url);
writeFileSync(fileURLToPath(url), JSON.stringify({ word, letter }) + "\n");
console.log(`word ranges ${word.length}, letter ranges ${letter.length}`);
