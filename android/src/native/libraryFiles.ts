/** Logo files in the app's documents folder, and picking an import file. */
import * as DocumentPicker from "expo-document-picker";
import { Directory, File, Paths } from "expo-file-system";
import { randomHex } from "../core/random";
import type { LogoStore } from "../services/library";

const logosDir = () => { const d = new Directory(Paths.document, "logos"); if (!d.exists) d.create({ intermediates: true }); return d; };

export const logoStore: LogoStore = {
  async save(base64, ext) { const f = new File(logosDir(), `${randomHex(8)}.${ext}`); f.write(base64, { encoding: "base64" }); return f.uri; },
  async read(uri) { const f = new File(uri); return f.exists ? f.base64() : null; },
  async copy(uri) {
    const target = new File(logosDir(), `${randomHex(8)}.${uri.split(".").pop() ?? "png"}`);
    await new File(uri).copy(target);
    return target.uri;
  },
  async remove(uri) { const f = new File(uri); if (f.exists) f.delete(); },
};

/** Let the user pick a file; returns its name and text, or null when cancelled. */
export async function pickTextFile(types: string[]): Promise<{ name: string; text: string } | null> {
  const r = await DocumentPicker.getDocumentAsync({ type: types, copyToCacheDirectory: true, multiple: false });
  if (r.canceled || !r.assets?.length) return null;
  const a = r.assets[0];
  return { name: a.name, text: await new File(a.uri).text() };
}
