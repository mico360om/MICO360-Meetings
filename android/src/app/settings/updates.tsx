import { Stack } from "expo-router";
import { useRef, useState } from "react";
import { Linking, Switch, View } from "react-native";
import type { AndroidUpdate } from "../../core/updater";
import { currentVersion, downloadUpdate, fetchJson, openInstaller } from "../../native/updates";
import { checkForUpdate } from "../../services/updates";
import { Banner, Button, Card, ProgressBar, Screen, T } from "../../ui/components";
import { errorText, useSettings } from "../../ui/data";
import { usePalette } from "../../ui/theme";

export default function Updates() {
  const c = usePalette();
  const { settings, update } = useSettings();
  const [found, setFound] = useState<AndroidUpdate | null>(null);
  const [state, setState] = useState<"idle" | "checking" | "latest" | "downloading">("idle");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const ctl = useRef<AbortController | null>(null);

  const check = async () => {
    setState("checking"); setError("");
    const r = await checkForUpdate(fetchJson, currentVersion());
    await update("lastUpdateCheck", Date.now());
    setFound(r.update); setError(r.error);
    setState(r.update || r.error ? "idle" : "latest");
  };

  const install = async () => {
    if (!found) return;
    setState("downloading"); setError(""); setProgress(0);
    ctl.current = new AbortController();
    try {
      const apk = await downloadUpdate(found, setProgress, ctl.current.signal);
      await openInstaller(apk);
    } catch (e) { setError(errorText(e)); } finally { setState("idle"); }
  };

  return (
    <Screen>
      <Stack.Screen options={{ title: "Updates" }} />
      <Card>
        <T>This version: {currentVersion()}</T>
        {state === "latest" ? <T muted size={13}>You have the latest version.</T> : null}
        <Button small kind="secondary" title="Check for updates" busy={state === "checking"} onPress={check} />
      </Card>
      {error ? <Banner kind="error" text={error} /> : null}
      {found ? (
        <Card>
          <T bold>Version {found.version} is available</T>
          {found.notes ? <T size={13}>{found.notes.slice(0, 1200)}</T> : null}
          {state === "downloading" ? (
            <View style={{ gap: 6 }}>
              <ProgressBar value={progress} label={`Downloading… ${Math.round(progress * 100)}%`} />
              <Button small kind="secondary" title="Cancel" onPress={() => ctl.current?.abort()} />
            </View>
          ) : (
            <View style={{ gap: 8 }}>
              <Button title="Download and install" onPress={install} />
              <Button kind="ghost" title="Release page" onPress={() => void Linking.openURL(found.pageUrl)} />
            </View>
          )}
          <T muted size={12}>The download is checked against the release's SHA256 before installing. Android may ask you to allow installs from MICO360 Meetings.</T>
        </Card>
      ) : null}
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <Switch value={settings.autoCheckUpdates} onValueChange={(v) => void update("autoCheckUpdates", v)} trackColor={{ true: c.accent }} accessibilityLabel="Check for updates automatically" />
        <T>Check automatically (once a day)</T>
      </View>
    </Screen>
  );
}
