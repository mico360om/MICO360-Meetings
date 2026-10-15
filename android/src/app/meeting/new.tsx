import { File } from "expo-file-system";
import { router } from "expo-router";
import { useEffect, useState } from "react";
import { Alert, View } from "react-native";
import { listMeetingTypes, listProfiles } from "../../data/library";
import { createMeeting, type Meeting } from "../../data/meetings";
import { importMediaFile } from "../../native/device";
import { newMeetingDefaults } from "../../services/meetingFlow";
import { Button, Card, Chip, Chips, Field, Screen, Section, T } from "../../ui/components";
import { errorText, notifyChanged, useDb, useQuery } from "../../ui/data";

type Source = "record" | "import" | "paste";

const today = () => new Date().toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

export default function NewMeeting() {
  const db = useDb();
  const [source, setSource] = useState<Source | null>(null);
  const [file, setFile] = useState<{ uri: string; name: string } | null>(null);
  const [title, setTitle] = useState("");
  const [date, setDate] = useState(today());
  const [attendees, setAttendees] = useState("");
  const [text, setText] = useState("");
  const [type, setType] = useState("");
  const [defaults, setDefaults] = useState<Partial<Meeting>>({});
  const [busy, setBusy] = useState(false);
  const types = useQuery(listMeetingTypes).data ?? [];
  const profiles = useQuery(listProfiles).data ?? [];

  useEffect(() => { newMeetingDefaults(db, type || undefined).then(setDefaults).catch(() => undefined); }, [db, type]);

  const pick = async (s: Source) => {
    if (s === "import") {
      try {
        const f = await importMediaFile();
        if (!f) return;
        setFile(f);
        if (!title) setTitle(f.name.replace(/\.[^.]+$/, ""));
      } catch (e) { Alert.alert("Couldn't import the file", errorText(e)); return; }
    }
    setSource(s);
  };

  const start = async () => {
    setBusy(true);
    try {
      const m = await createMeeting(db, { ...defaults, title: title.trim(), meetingDate: date.trim(), attendees: attendees.trim(),
        source: source ?? "", audioPath: file?.uri ?? "", transcript: source === "paste" ? text.trim() : "" });
      notifyChanged();
      if (source === "record") router.replace({ pathname: "/meeting/record", params: { id: m.id } });
      else if (source === "import") router.replace(`/meeting/${m.id}/transcribe`);
      else router.replace(`/meeting/${m.id}`);
    } catch (e) {
      Alert.alert("Couldn't create the meeting", errorText(e));
    } finally { setBusy(false); }
  };

  if (!source) {
    return (
      <Screen>
        <T size={17} bold>How do you want to start?</T>
        <Card label="Record a meeting" onPress={() => pick("record")}>
          <T bold>Record</T><T muted size={13}>Record with this phone's microphone — keeps going with the screen off.</T>
        </Card>
        <Card label="Import an audio or video file" onPress={() => pick("import")}>
          <T bold>Import a file</T><T muted size={13}>An audio or video recording from this phone, transcribed on the phone.</T>
        </Card>
        <Card label="Paste a transcript" onPress={() => pick("paste")}>
          <T bold>Paste text</T><T muted size={13}>A transcript or notes you already have.</T>
        </Card>
      </Screen>
    );
  }

  const company = profiles.find((p) => p.id === defaults.profileId);
  return (
    <Screen>
      {file ? <T muted>File: {file.name}</T> : null}
      <Field label="Meeting title" value={title} onChangeText={setTitle} placeholder="e.g. Weekly operations review" />
      <Field label="Date and time" value={date} onChangeText={setDate} />
      <Field label="Attendees" value={attendees} onChangeText={setAttendees} placeholder="Names, separated by commas" />
      {types.length ? (
        <Section title="Meeting type">
          <Chips>
            <Chip title="None" selected={!type} onPress={() => setType("")} label="Meeting type none" />
            {types.map((t) => <Chip key={t.name} title={t.name} selected={type === t.name} onPress={() => setType(t.name)} label={`Meeting type ${t.name}`} />)}
          </Chips>
        </Section>
      ) : null}
      {profiles.length ? (
        <Section title="Company">
          <Chips>
            <Chip title="No branding" selected={!defaults.profileId} onPress={() => setDefaults({ ...defaults, profileId: "" })} />
            {profiles.map((p) => <Chip key={p.id} title={p.name} selected={company?.id === p.id} onPress={() => setDefaults({ ...defaults, profileId: p.id })} />)}
          </Chips>
        </Section>
      ) : null}
      <T muted size={13}>Template: {defaults.promptName || "—"}</T>
      {source === "paste" ? <Field label="Transcript" value={text} onChangeText={setText} multiline placeholder="Paste the transcript here" style={{ minHeight: 200 }} /> : null}
      <View style={{ gap: 8, marginTop: 8 }}>
        <Button busy={busy} disabled={source === "paste" && !text.trim()}
          title={source === "record" ? "Start recording" : source === "import" ? "Transcribe the file" : "Continue"}
          label="Start meeting" onPress={start} />
        <Button kind="ghost" title="Back" onPress={() => {
          if (file) { const f = new File(file.uri); if (f.exists) f.delete(); }   // the copy made at import
          setSource(null); setFile(null);
        }} />
      </View>
    </Screen>
  );
}
