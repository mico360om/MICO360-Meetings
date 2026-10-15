import { Stack } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { View } from "react-native";
import { type ModelId, type WhisperModel, WHISPER_MODELS } from "../../core/models";
import { lowStorageWarning } from "../../native/device";
import { deleteModel, downloadModel, installedModels } from "../../native/modelFiles";
import { Banner, Button, Card, Chip, Chips, ProgressBar, Screen, Section, T } from "../../ui/components";
import { errorText, useSettings } from "../../ui/data";
import { confirm } from "../../ui/hooks";

export default function TranscriptionSettings() {
  const { settings, update } = useSettings();
  const [installed, setInstalled] = useState<ModelId[]>([]);
  const [downloading, setDownloading] = useState<{ id: ModelId; fraction: number } | null>(null);
  const [error, setError] = useState("");
  const ctl = useRef<AbortController | null>(null);

  const refresh = () => installedModels().then(setInstalled).catch(() => undefined);
  useEffect(() => { void refresh(); return () => ctl.current?.abort(); }, []);

  const download = async (m: WhisperModel) => {
    setError("");
    const low = lowStorageWarning();
    if (low && !(await confirm("Low storage", low, "Download anyway"))) return;
    ctl.current = new AbortController();
    setDownloading({ id: m.id, fraction: 0 });
    try {
      await downloadModel(m, (fraction) => setDownloading({ id: m.id, fraction }), ctl.current.signal);
      await update("whisperModel", m.id);
    } catch (e) { setError(errorText(e)); } finally { setDownloading(null); void refresh(); }
  };

  const remove = async (m: WhisperModel) => {
    if (!(await confirm(`Delete the ${m.id} model?`, "You can download it again later.", "Delete", true))) return;
    deleteModel(m);
    void refresh();
  };

  return (
    <Screen>
      <Stack.Screen options={{ title: "Transcription" }} />
      <T muted size={13}>Transcription runs on this phone. Download a speech model once; after that no internet is needed to transcribe.</T>
      {error ? <Banner kind="error" text={error} /> : null}
      <Section title="Speech model">
        {WHISPER_MODELS.map((m) => {
          const has = installed.includes(m.id);
          const active = settings.whisperModel === m.id;
          return (
            <Card key={m.id} label={`Speech model ${m.id}`}>
              <T bold>{m.label}</T>
              <T muted size={13}>{has ? (active ? "Downloaded · in use" : "Downloaded") : "Not downloaded"}</T>
              {downloading?.id === m.id ? (
                <View style={{ gap: 6 }}>
                  <ProgressBar value={downloading.fraction} label={downloading.fraction >= 0.999 ? "Verifying the download…" : `Downloading… ${Math.round(downloading.fraction * 100)}%`} />
                  <Button small kind="secondary" title="Pause" label="Pause download" onPress={() => ctl.current?.abort()} />
                </View>
              ) : (
                <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
                  {!has ? <Button small title="Download" label={`Download ${m.id} model`} disabled={!!downloading} onPress={() => download(m)} /> : null}
                  {has && !active ? <Button small kind="secondary" title="Use this model" onPress={() => void update("whisperModel", m.id)} /> : null}
                  {has ? <Button small kind="ghost" title="Delete" onPress={() => remove(m)} /> : null}
                </View>
              )}
            </Card>
          );
        })}
      </Section>
      <Section title="Language">
        <Chips>
          {([["auto", "Detect automatically"], ["en", "English"], ["ar", "Arabic"]] as const).map(([k, t]) =>
            <Chip key={k} title={t} selected={settings.language === k} onPress={() => void update("language", k)} />)}
        </Chips>
      </Section>
      <Section title="Speakers">
        <Chips>
          <Chip title="Identify speakers" selected={settings.diarize} onPress={() => void update("diarize", true)} />
          <Chip title="Off" selected={!settings.diarize} onPress={() => void update("diarize", false)} />
        </Chips>
        <T muted size={13}>Labels each part of the transcript Speaker 1, Speaker 2… so you can put names on them.</T>
      </Section>
    </Screen>
  );
}
