import Constants from "expo-constants";
import { router } from "expo-router";
import { Switch } from "react-native";
import { cloudEnabled } from "../../native/cloud";
import { syncReminders } from "../../native/device";
import { Card, Row, Screen, Section } from "../../ui/components";
import { useDb, useSettings } from "../../ui/data";
import { usePalette } from "../../ui/theme";

export default function Settings() {
  const db = useDb();
  const c = usePalette();
  const { settings, update } = useSettings();
  const toggle = (on: boolean, flip: () => void) => <Switch value={on} onValueChange={flip} trackColor={{ true: c.accent }} thumbColor={c.card} accessibilityRole="switch" />;
  const fillers = () => void update("removeFillers", !settings.removeFillers);
  const reminders = async () => { await update("reminders", !settings.reminders); await syncReminders(db); };
  return (
    <Screen>
      <Section title="Meetings">
        <Card>
          <Row title="Transcription" detail={`Speech model: ${settings.whisperModel} · language: ${settings.language}`} onPress={() => router.push("/settings/transcription")} />
          <Row title="Companies" detail="Letterhead, logo and PDF design" onPress={() => router.push("/settings/companies")} />
          <Row title="Prompts" detail="Templates for the minutes" onPress={() => router.push("/settings/prompts")} />
          <Row title="Meeting types" detail="Template, style and company per kind of meeting" onPress={() => router.push("/settings/types")} />
          <Row title="Import & export" detail="Prompts and companies, compatible with the desktop app" onPress={() => router.push("/settings/transfer")} />
        </Card>
      </Section>
      <Section title="Minutes">
        <Card>
          <Row title="MICO360 Cloud" detail={cloudEnabled() ? "Status, model and connection" : "Not enabled in this build"} onPress={() => router.push("/settings/cloud")} />
          <Row title="Remove filler words" detail="um, uh, you know… before generating" label="Remove filler words"
            onPress={fillers} right={toggle(settings.removeFillers, fillers)} />
        </Card>
      </Section>
      <Section title="Reminders">
        <Card>
          <Row title="Action item reminders" detail="The day before and on the day, around 09:00" label="Action item reminders"
            onPress={reminders} right={toggle(settings.reminders, () => void reminders())} />
        </Card>
      </Section>
      <Section title="App">
        <Card>
          <Row title="Updates" detail="Check for a newer version" onPress={() => router.push("/settings/updates")} />
          <Row title="About & privacy" detail={`Version ${Constants.expoConfig?.version ?? ""}`} onPress={() => router.push("/settings/about")} />
        </Card>
      </Section>
    </Screen>
  );
}
