import { AudioRecorderProvider } from "@siteed/audio-studio";
import { getRandomBytes } from "expo-crypto";
import { router, Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { Alert } from "react-native";
import { setRandomSource } from "../core/random";
import { probeCloud } from "../native/cloud";
import { getSettings, setSetting } from "../data/settings";
import { currentVersion, fetchJson } from "../native/updates";
import { checkForUpdate, shouldAutoCheck } from "../services/updates";
import { DbProvider, useDb } from "../ui/data";
import { usePalette } from "../ui/theme";

setRandomSource(getRandomBytes);   // Hermes has no WebCrypto: request ids and record ids need a secure source

/** Once a day, quietly look for a newer version and offer it. */
function StartupTasks() {
  const db = useDb();
  useEffect(() => {
    (async () => {
      const s = await getSettings(db);
      if (!shouldAutoCheck(s, Date.now())) return;
      const r = await checkForUpdate(fetchJson, currentVersion());
      await setSetting(db, "lastUpdateCheck", Date.now());
      if (r.update) Alert.alert(`Version ${r.update.version} is available`, "Download and install it from Settings → Updates.", [
        { text: "Later", style: "cancel" }, { text: "View", onPress: () => router.push("/settings/updates") },
      ]);
    })().catch(() => undefined);
  }, [db]);
  return null;
}

export default function RootLayout() {
  const c = usePalette();
  useEffect(() => { probeCloud().catch(() => undefined); }, []);
  return (
    <DbProvider>
      <AudioRecorderProvider>
        <StartupTasks />
        <StatusBar style="auto" />
        <Stack screenOptions={{
          headerStyle: { backgroundColor: c.card }, headerTintColor: c.text, headerTitleStyle: { fontWeight: "600" },
          contentStyle: { backgroundColor: c.bg },
        }}>
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
          <Stack.Screen name="meeting/new" options={{ title: "New meeting" }} />
          <Stack.Screen name="meeting/record" options={{ title: "Recording", gestureEnabled: false }} />
          <Stack.Screen name="meeting/[id]/index" options={{ title: "Meeting" }} />
          <Stack.Screen name="meeting/[id]/transcribe" options={{ title: "Transcribe" }} />
          <Stack.Screen name="meeting/[id]/export" options={{ title: "Export & share" }} />
        </Stack>
      </AudioRecorderProvider>
    </DbProvider>
  );
}
