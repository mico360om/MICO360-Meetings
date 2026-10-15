import { File } from "expo-file-system";
import { router, Stack, useLocalSearchParams } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { View } from "react-native";
import { MUTED_MIC_MESSAGE } from "../../core/silence";
import { formatTime } from "../../core/transcribeLong";
import { updateMeeting } from "../../data/meetings";
import { lowStorageWarning, openAppSettings, requestMicrophone } from "../../native/device";
import { ensureNotificationPermission } from "../../native/notify";
import { useMeetingRecorder } from "../../native/recorder";
import { Banner, Button, ProgressBar, Screen, T } from "../../ui/components";
import { errorText, notifyChanged, useDb } from "../../ui/data";
import { confirm, useLeaveGuard } from "../../ui/hooks";

export default function Record() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const db = useDb();
  const rec = useMeetingRecorder();
  const [muted, setMuted] = useState(false);
  const [interrupted, setInterrupted] = useState("");
  const [denied, setDenied] = useState<"" | "denied" | "blocked">("");
  const [error, setError] = useState("");
  const [stopping, setStopping] = useState(false);
  const started = useRef(false);
  const recording = useRef(false);          // true from a successful start until stop or discard
  const [storage] = useState(lowStorageWarning);

  const begin = async () => {
    setError("");
    const mic = await requestMicrophone();
    if (mic !== "granted") { setDenied(mic); return; }
    setDenied("");
    await ensureNotificationPermission().catch(() => false);   // the recording notification; recording works without it
    try {
      recording.current = true;
      await rec.start(() => setMuted(true), (reason, paused) => {
        if (!paused) { setInterrupted(""); return; }
        setInterrupted(reason === "phoneCall" ? "Recording paused for a phone call. Tap Resume when you're back."
          : "Recording paused because another app took the microphone. Tap Resume to continue.");
      });
    } catch (e) { recording.current = false; setError(errorText(e)); }
  };

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void begin();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stop = async () => {
    setStopping(true);
    try {
      recording.current = false;
      const r = await rec.stop();
      if (!r) throw new Error("The recording could not be saved.");
      await updateMeeting(db, id, { audioPath: r.uri, durationMs: r.durationMs, source: "record" });
      notifyChanged();
      router.replace(`/meeting/${id}/transcribe`);
    } catch (e) { setError(errorText(e)); setStopping(false); }
  };

  /** Stop and delete the audio; the meeting stays without it. */
  const discard = async () => {
    recording.current = false;
    const r = await rec.stop().catch(() => null);
    if (r) { const f = new File(r.uri); if (f.exists) f.delete(); }
  };
  const askDiscard = async () => confirm("Discard this recording?", "The audio is deleted; the meeting stays, without audio.", "Discard", true);
  const leave = async () => { if (await askDiscard()) { await discard(); router.replace(`/meeting/${id}`); } };
  // Android back or a gesture while recording: ask, never leave a recorder running without its screen.
  useLeaveGuard(() => recording.current, (go) => { void (async () => { if (await askDiscard()) { await discard(); go(); } })(); });

  return (
    <Screen scroll={false}>
      <Stack.Screen options={{ headerBackVisible: !rec.isRecording }} />
      {denied ? <Banner kind="error" text={denied === "blocked"
        ? "Microphone access is turned off for MICO360 Meetings. Allow it in Settings to record."
        : "MICO360 Meetings needs the microphone to record the meeting."}
        action={denied === "blocked" ? "Open settings" : "Try again"} onAction={denied === "blocked" ? openAppSettings : begin} /> : null}
      {denied === "blocked" ? <Button kind="secondary" title="Try again" onPress={begin} /> : null}
      {storage ? <Banner kind="warn" text={storage} /> : null}
      {muted ? <Banner kind="warn" text={MUTED_MIC_MESSAGE} /> : null}
      {interrupted && rec.isPaused ? <Banner kind="warn" text={interrupted} /> : null}
      {error ? <Banner kind="error" text={error} action="Try again" onAction={begin} /> : null}
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", gap: 16 }}>
        <T size={48} bold accessibilityLabel="recording time">{formatTime(rec.durationMs / 1000)}</T>
        <T muted>{rec.isPaused ? "Paused" : rec.isRecording ? "Recording — you can turn the screen off" : "Starting…"}</T>
        <View style={{ width: "80%" }}><ProgressBar value={Math.min(1, rec.level * 4)} /></View>
      </View>
      <View style={{ gap: 8 }}>
        {rec.isRecording ? (
          <Button kind="secondary" title={rec.isPaused ? "Resume" : "Pause"} onPress={() => void (rec.isPaused ? rec.resume() : rec.pause())} />
        ) : null}
        <Button title="Stop and transcribe" label="Stop recording" busy={stopping} disabled={!rec.isRecording} onPress={stop} />
        <Button kind="ghost" title="Discard" onPress={leave} />
      </View>
    </Screen>
  );
}
