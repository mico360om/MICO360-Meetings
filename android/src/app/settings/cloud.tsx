import { Stack } from "expo-router";
import { useEffect, useState } from "react";
import type { CloudStatus } from "../../core/cloudClient";
import { cloudEnabled, makeCloudClient, probeCloud } from "../../native/cloud";
import { Banner, Button, Card, Chip, Chips, Screen, Section, Spinner, T } from "../../ui/components";
import { errorText, useSettings } from "../../ui/data";

export default function CloudSettings() {
  const { settings, update } = useSettings();
  const [status, setStatus] = useState<CloudStatus | null>(null);
  const [encrypted, setEncrypted] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");

  const check = async () => {
    setChecking(true); setError("");
    try {
      await probeCloud();
      const client = makeCloudClient(settings);
      setEncrypted(client.isEncrypted());
      setStatus(await client.status());
    } catch (e) { setError(errorText(e)); } finally { setChecking(false); }
  };
  useEffect(() => { if (cloudEnabled()) void check(); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!cloudEnabled()) {
    return (
      <Screen>
        <Stack.Screen options={{ title: "MICO360 Cloud" }} />
        <Banner kind="warn" text="Minutes need MICO360 Cloud, which isn't enabled in this build. Recording, transcription, editing, exports and action items all work." />
      </Screen>
    );
  }
  return (
    <Screen>
      <Stack.Screen options={{ title: "MICO360 Cloud" }} />
      <T muted size={13}>Transcription happens on this phone. Only the transcript is sent to MICO360 Cloud to write the minutes.</T>
      {error ? <Banner kind="error" text={error} /> : null}
      <Card>
        {checking && !status ? <Spinner /> : null}
        {status ? (
          <>
            <T bold>{status.running ? "Connected" : "Not reachable"}</T>
            {status.error ? <T muted size={13}>{status.error}</T> : null}
            <T muted size={13}>{encrypted ? "Encrypted connection (HTTPS)." : "Unencrypted connection — you are asked before a transcript is sent."}</T>
          </>
        ) : null}
        <Button small kind="secondary" title="Check again" busy={checking} onPress={check} />
      </Card>
      {status?.models.length ? (
        <Section title="Model">
          <Chips>
            <Chip title="Default" selected={!settings.cloudModel} onPress={() => void update("cloudModel", "")} />
            {status.models.map((m) => <Chip key={m} title={m} selected={settings.cloudModel === m} onPress={() => void update("cloudModel", m)} />)}
          </Chips>
        </Section>
      ) : null}
      {settings.plainHttpConsent ? (
        <Button kind="ghost" title="Ask again before sending unencrypted" onPress={() => void update("plainHttpConsent", false)} />
      ) : null}
    </Screen>
  );
}
