/** Small device helpers the screens share: permissions, media import,
 *  the transcription adapters, and keeping reminders in step with the data. */
import * as DocumentPicker from "expo-document-picker";
import { File } from "expo-file-system";
import { Linking, PermissionsAndroid } from "react-native";
import { randomHex } from "../core/random";
import type { Db } from "../data/db";
import { reminderPlans } from "../services/meetingFlow";
import type { TranscriptionDeps } from "../services/transcription";
import { fileDurationMs, fileSource } from "./decode";
import { modelPath } from "./modelFiles";
import { ensureNotificationPermission, scheduleReminders } from "./notify";
import { recordingsDir } from "./recorder";
import { whisperRecognizer } from "./whisper";

export const transcriptionDeps: TranscriptionDeps = {
  modelPath, recognizer: whisperRecognizer, source: fileSource, durationMs: fileDurationMs,
};

/** "granted", "denied" (ask again later) or "blocked" (only Settings can change it). */
export async function requestMicrophone(): Promise<"granted" | "denied" | "blocked"> {
  const r = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO, {
    title: "Microphone", message: "MICO360 Meetings records meetings with the microphone. Audio stays on this phone.",
    buttonPositive: "Allow",
  });
  return r === PermissionsAndroid.RESULTS.GRANTED ? "granted" : r === PermissionsAndroid.RESULTS.NEVER_ASK_AGAIN ? "blocked" : "denied";
}

export const openAppSettings = () => Linking.openSettings();

/** Let the user pick an audio or video file; it is copied into the app's recordings. */
export async function importMediaFile(): Promise<{ uri: string; name: string } | null> {
  const r = await DocumentPicker.getDocumentAsync({ type: ["audio/*", "video/*"], copyToCacheDirectory: true, multiple: false });
  if (r.canceled || !r.assets?.length) return null;
  const a = r.assets[0];
  const dir = recordingsDir();
  if (!dir.exists) dir.create({ intermediates: true });
  const ext = (a.name.split(".").pop() ?? "m4a").toLowerCase().replace(/[^a-z0-9]/g, "") || "m4a";
  const target = new File(dir, `import-${randomHex(6)}.${ext}`);
  await new File(a.uri).copy(target);
  return { uri: target.uri, name: a.name };
}

/** Re-plan every action-item reminder (after any change to items or settings). */
export async function syncReminders(db: Db): Promise<void> {
  try {
    const plans = await reminderPlans(db, new Date());
    if (plans.length && !(await ensureNotificationPermission())) return;
    await scheduleReminders(plans);
  } catch { /* reminders are best effort; the Action Items tab still shows what is due */ }
}
