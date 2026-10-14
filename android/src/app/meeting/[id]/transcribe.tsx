import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { View } from "react-native";
import { CancelledError } from "../../../core/pipeline";
import { getMeeting } from "../../../data/meetings";
import { getSettings } from "../../../data/settings";
import { transcriptionDeps } from "../../../native/device";
import { saveTranscript } from "../../../services/meetingFlow";
import { ModelMissing, transcribeMeetingAudio } from "../../../services/transcription";
import { Banner, Button, ProgressBar, Screen, T } from "../../../ui/components";
import { errorText, notifyChanged, useDb } from "../../../ui/data";
import { confirm, useLeaveGuard } from "../../../ui/hooks";

export default function Transcribe() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const db = useDb();
  const [progress, setProgress] = useState<{ value: number | null; text: string }>({ value: null, text: "Preparing…" });
  const [error, setError] = useState("");
  const [missing, setMissing] = useState(false);
  const [running, setRunning] = useState(false);
  const ctl = useRef<AbortController | null>(null);
  const busy = useRef(false);
  useLeaveGuard(() => busy.current, (go) => {
    void confirm("Stop transcribing?", "The transcription so far will be lost; the recording is kept and you can transcribe it later.", "Stop", true)
      .then((yes) => { if (yes) { busy.current = false; ctl.current?.abort(); go(); } });
  });

  const run = async () => {
    setError(""); setMissing(false); setRunning(true); busy.current = true;
    ctl.current = new AbortController();
    try {
      const m = await getMeeting(db, id);
      if (!m?.audioPath) throw new Error("This meeting has no recording to transcribe.");
      const settings = await getSettings(db);
      const t = await transcribeMeetingAudio(m.audioPath, settings, transcriptionDeps, {
        signal: ctl.current.signal,
        progress: (fraction, text) => setProgress({ value: fraction > 0 && fraction < 1 ? fraction : null, text }),
      });
      await saveTranscript(db, id, t);
      notifyChanged();
      busy.current = false;
      router.replace(`/meeting/${id}`);
    } catch (e) {
      if (e instanceof ModelMissing) setMissing(true);
      else if (e instanceof CancelledError) setError("Transcription cancelled. The recording is kept — you can transcribe it later.");
      else setError(errorText(e));
    } finally { setRunning(false); busy.current = false; }
  };

  useEffect(() => { void run(); return () => ctl.current?.abort(); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Screen>
      {missing ? <Banner kind="warn" text="The speech model isn't downloaded yet. Download it once (Wi-Fi recommended); then transcription works offline."
        action="Download in Settings" onAction={() => router.push("/settings/transcription")} /> : null}
      {error ? <Banner kind="error" text={error} /> : null}
      {running ? (
        <View style={{ gap: 12 }}>
          <T bold>Transcribing on this phone</T>
          <ProgressBar value={progress.value} label={progress.text} />
          <T muted size={13}>Long meetings take a while. You can leave this screen open with the phone charging.</T>
          <Button kind="secondary" title="Cancel" onPress={() => ctl.current?.abort()} />
        </View>
      ) : (
        <View style={{ gap: 8 }}>
          <Button title="Transcribe" label="Start transcription" onPress={run} />
          <Button kind="ghost" title="Open the meeting" onPress={() => router.replace(`/meeting/${id}`)} />
        </View>
      )}
    </Screen>
  );
}
