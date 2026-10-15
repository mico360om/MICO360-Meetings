/** The four PDF designs a company can choose (data from desktop export/pdf_designs.py). */
import data from "./data/desktop.json";

export interface Design {
  key: string; name: string; blurb: string; font: string; body: number; lead: number; cell: number;
  title_size: number; title_align: string; h2_size: number; gap: number; letterhead: string; logo_max_mm: number;
  name_max: number; name_accent: boolean; top_band_mm: number; heading: string; details: string; table: string;
  status_color: boolean; signatures: boolean;
}

export const DESIGNS: Design[] = data.pdfDesigns as Design[];

export function getDesign(key?: string | null): Design {
  const k = String(key ?? "").trim().toLowerCase();
  return DESIGNS.find((d) => d.key === k) ?? DESIGNS.find((d) => d.key === data.defaultPdfDesign)!;
}
