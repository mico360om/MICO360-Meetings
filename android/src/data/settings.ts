import type { Db } from "./db";

export interface AppSettings {
  whisperModel: "tiny" | "base" | "small";
  language: "auto" | "en" | "ar";
  diarize: boolean;
  removeFillers: boolean;
  chunkChars: number;
  cloudModel: string;
  plainHttpConsent: boolean;
  reminders: boolean;
  defaultProfileId: string;
  defaultPromptName: string;
  defaultStyle: string;
  lastUpdateCheck: number;
  autoCheckUpdates: boolean;
}

export const DEFAULT_SETTINGS: AppSettings = {
  whisperModel: "base", language: "auto", diarize: false, removeFillers: true, chunkChars: 6000, cloudModel: "",
  plainHttpConsent: false, reminders: true, defaultProfileId: "", defaultPromptName: "Formal Minutes",
  defaultStyle: "Formal Minutes", lastUpdateCheck: 0, autoCheckUpdates: true,
};

/** Stored settings over the defaults; a value of the wrong type keeps the default. */
export async function getSettings(db: Db): Promise<AppSettings> {
  const rows = await db.getAllAsync<{ key: string; value: string }>("SELECT key, value FROM settings");
  const out: AppSettings = { ...DEFAULT_SETTINGS };
  for (const r of rows) {
    if (!(r.key in DEFAULT_SETTINGS)) continue;
    try {
      const v = JSON.parse(r.value);
      const key = r.key as keyof AppSettings;
      if (typeof v === typeof DEFAULT_SETTINGS[key]) (out as unknown as Record<string, unknown>)[key] = v;
    } catch { /* keep the default */ }
  }
  return out;
}

export async function setSetting<K extends keyof AppSettings>(db: Db, key: K, value: AppSettings[K]): Promise<void> {
  await db.runAsync("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", [key, JSON.stringify(value)]);
}
