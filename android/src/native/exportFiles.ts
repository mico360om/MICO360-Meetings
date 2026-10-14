/** Export files on the phone: A4 PDF via the print engine, other formats
 *  written to the cache, then handed to Android's share sheet. */
import { Directory, File, Paths } from "expo-file-system";
import * as Print from "expo-print";
import * as Sharing from "expo-sharing";
import { Image } from "react-native";
import type { CompanyProfile } from "../core/profiles";
import type { ExportDeps, LogoData } from "../services/exports";

const exportsDir = () => { const d = new Directory(Paths.cache, "exports"); if (!d.exists) d.create({ intermediates: true }); return d; };
const fresh = (name: string) => { const f = new File(exportsDir(), name); if (f.exists) f.delete(); return f; };

function sizeOf(uri: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => Image.getSize(uri, (width, height) => resolve({ width, height }), () => resolve({ width: 0, height: 0 })));
}

export async function logoData(profile: CompanyProfile): Promise<LogoData | null> {
  if (!profile.logo_path) return null;
  const f = new File(profile.logo_path);
  if (!f.exists) return null;
  const ext = (f.uri.split(".").pop() ?? "").toLowerCase();
  const type = ext === "jpeg" || ext === "jpg" ? "jpg" : ext === "gif" ? "gif" : ext === "bmp" ? "bmp" : "png";
  const base64 = await f.base64();
  const { width, height } = await sizeOf(f.uri);
  return { dataUri: `data:image/${type === "jpg" ? "jpeg" : type};base64,${base64}`, base64, type, widthPx: width, heightPx: height };
}

export const nativeExportDeps: ExportDeps = {
  async pdfFromHtml(html) { return (await Print.printToFileAsync({ html, width: 595, height: 842 })).uri; },
  async writeText(name, text) { const f = fresh(name); f.write(text); return f.uri; },
  async writeBase64(name, b64) { const f = fresh(name); f.write(b64, { encoding: "base64" }); return f.uri; },
  async moveTo(uri, name) { const target = fresh(name); await new File(uri).move(target); return target.uri; },
  logo: logoData,
};

export async function shareFile(uri: string, mimeType: string, dialogTitle: string): Promise<void> {
  if (!(await Sharing.isAvailableAsync())) throw new Error("Sharing isn't available on this device.");
  await Sharing.shareAsync(uri, { mimeType, dialogTitle });
}
