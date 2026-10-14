import { router, Stack, useLocalSearchParams } from "expo-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { ScrollView, TextInput, useColorScheme, View } from "react-native";
import { WebView } from "react-native-webview";
import { renderPreviewHtml } from "../../../core/exportHtml";
import { CancelledError } from "../../../core/pipeline";
import { STYLE_NAMES } from "../../../core/prompts";
import { hasArabic } from "../../../core/rtl";
import { listProfiles, listPrompts } from "../../../data/library";
import { getMeeting, type Meeting, updateMeeting } from "../../../data/meetings";
import { getSettings, setSetting } from "../../../data/settings";
import { cloudEnabled, makeCloudClient } from "../../../native/cloud";
import { syncReminders } from "../../../native/device";
import { generateForMeeting, renameSpeakers } from "../../../services/meetingFlow";
import { Banner, Button, Card, Chip, Chips, Field, ProgressBar, Screen, Section, Spinner, T, rtlStyle } from "../../../ui/components";
import { errorText, notifyChanged, useDb, useQuery } from "../../../ui/data";
import { insertBlock, prefixLines, type Sel, TABLE_TEMPLATE, wrap } from "../../../ui/editing";
import { confirm, useAutosave } from "../../../ui/hooks";
import { radius, usePalette } from "../../../ui/theme";

type View3 = "transcript" | "minutes" | "preview";

export default function MeetingScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const db = useDb();
  const c = usePalette();
  const dark = useColorScheme() === "dark";
  const [m, setM] = useState<Meeting | null>(null);
  const [missing, setMissing] = useState(false);
  const [view, setView] = useState<View3>("transcript");
  const [details, setDetails] = useState({ title: "", meetingDate: "", attendees: "" });
  const [transcript, setTranscript] = useState("");
  const [minutes, setMinutes] = useState("");
  const [sel, setSel] = useState<Sel>({ start: 0, end: 0 });
  const [forcedSel, setForcedSel] = useState<Sel | undefined>();   // only right after a toolbar edit
  const history = useRef<string[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [gen, setGen] = useState<{ value: number | null; text: string } | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [showDetails, setShowDetails] = useState(false);
  const ctl = useRef<AbortController | null>(null);
  const prompts = useQuery(listPrompts).data ?? [];
  const profiles = useQuery(listProfiles).data ?? [];

  useEffect(() => {
    getMeeting(db, id).then((x) => {
      if (!x) { setMissing(true); return; }
      setM(x);
      setDetails({ title: x.title, meetingDate: x.meetingDate, attendees: x.attendees });
      setTranscript(x.transcript);
      setMinutes(x.minutes);
      if (x.minutes.trim()) setView("minutes");
    }).catch((e) => setError(errorText(e)));
  }, [db, id]);

  const save = async (patch: Partial<Meeting>) => {
    const next = await updateMeeting(db, id, patch);
    setM(next);
    notifyChanged();
    return next;
  };
  const loaded = !!m;
  const detailsSave = useAutosave(details, (d) => save(d), loaded);
  const transcriptSave = useAutosave(transcript, (t) => save({ transcript: t }), loaded);
  const minutesSave = useAutosave(minutes, async (t) => { await save({ minutes: t }); await syncReminders(db); }, loaded);

  const speakers = useMemo(() => [...new Set((m?.segments ?? []).map((s) => s.speaker).filter((s): s is string => !!s))], [m]);
  const company = profiles.find((p) => p.id === m?.profileId) ?? null;

  if (missing) return <Screen><Banner kind="error" text="This meeting no longer exists." /></Screen>;
  if (!m) return <Screen><Spinner /></Screen>;

  const edit = (next: { text: string; sel: Sel }) => {
    history.current.push(minutes);
    if (history.current.length > 50) history.current.shift();
    setMinutes(next.text);
    setSel(next.sel);
    setForcedSel(next.sel);
  };
  const undo = () => { const prev = history.current.pop(); if (prev !== undefined) setMinutes(prev); };

  const generate = async () => {
    setError(""); setWarnings([]);
    if (!transcript.trim()) { setError("Add a transcript first — record, import or paste one."); return; }
    await transcriptSave.flush(); await detailsSave.flush();
    if (minutes.trim() && !(await confirm("Replace the minutes?", "The current minutes will be replaced by newly generated ones.", "Replace"))) return;
    const settings = await getSettings(db);
    const client = makeCloudClient(settings);
    if (!client.isEncrypted() && !settings.plainHttpConsent) {
      const ok = await confirm("Unencrypted connection", "MICO360 Cloud is reached over an unencrypted connection right now, so your transcript would be sent unencrypted. Continue?");
      if (!ok) return;
      await setSetting(db, "plainHttpConsent", true);
    }
    ctl.current = new AbortController();
    setGen({ value: null, text: "Sending to MICO360 Cloud…" });
    try {
      const r = await generateForMeeting(db, id, client, {
        signal: ctl.current.signal,
        progress: (fraction, text) => setGen({ value: fraction > 0 && fraction < 1 ? fraction : null, text }),
      });
      history.current.push(minutes);
      setM(r.meeting);
      setMinutes(r.meeting.minutes);
      setWarnings(r.warnings);
      setView("minutes");
      notifyChanged();
      await syncReminders(db);
    } catch (e) {
      setError(e instanceof CancelledError ? "Generation cancelled. Completed parts are remembered — Generate again continues from them." : errorText(e));
    } finally { setGen(null); }
  };

  const applyNames = async () => {
    try {
      const r = await renameSpeakers(db, id, names);
      setM(r); setTranscript(r.transcript); setNames({}); notifyChanged();
    } catch (e) { setError(errorText(e)); }
  };

  const toggleFinal = () => void save({ status: m.status === "final" ? "draft" : "final" });

  const previewHtml = `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{font-family:Roboto,"Noto Sans","Noto Naskh Arabic",sans-serif;padding:14px;margin:0;color:${c.text};background:${c.card};line-height:1.45}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid ${c.border};padding:4px 6px}</style></head>
<body>${renderPreviewHtml(minutes, company?.accent_color ?? (dark ? "#D0605C" : null))}</body></html>`;

  const tool = (title: string, f: () => void) => <Button small kind="secondary" title={title} label={`Format ${title}`} onPress={f} />;

  return (
    <Screen>
      <Stack.Screen options={{ title: details.title || "Untitled meeting" }} />
      <Card>
        <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
          <View style={{ flex: 1 }}>
            <T bold>{details.title || "Untitled meeting"}</T>
            <T muted size={13}>{[details.meetingDate, m.status === "final" ? "Final" : "Draft", company?.name].filter(Boolean).join(" · ")}</T>
          </View>
          <Button small kind="ghost" title={showDetails ? "Done" : "Details"} label="Edit meeting details" onPress={() => setShowDetails(!showDetails)} />
        </View>
        {showDetails ? (
          <View style={{ gap: 10, marginTop: 8 }}>
            <Field label="Meeting title" value={details.title} onChangeText={(t) => setDetails({ ...details, title: t })} />
            <Field label="Date and time" value={details.meetingDate} onChangeText={(t) => setDetails({ ...details, meetingDate: t })} />
            <Field label="Attendees" value={details.attendees} onChangeText={(t) => setDetails({ ...details, attendees: t })} />
            <Section title="Template">
              <ScrollView horizontal showsHorizontalScrollIndicator={false}><View style={{ flexDirection: "row", gap: 8 }}>
                {prompts.map((p) => <Chip key={p.id} title={p.name} selected={m.promptName === p.name} onPress={() => void save({ promptName: p.name })} />)}
              </View></ScrollView>
            </Section>
            <Section title="Style">
              <Chips>{STYLE_NAMES.map((s) => <Chip key={s} title={s} selected={(m.style || "") === s} onPress={() => void save({ style: s })} />)}</Chips>
            </Section>
            <Section title="Company">
              <Chips>
                <Chip title="No branding" selected={!m.profileId} onPress={() => void save({ profileId: "" })} />
                {profiles.map((p) => <Chip key={p.id} title={p.name} selected={m.profileId === p.id} onPress={() => void save({ profileId: p.id })} />)}
              </Chips>
            </Section>
          </View>
        ) : null}
      </Card>

      <Chips>
        <Chip title="Transcript" selected={view === "transcript"} onPress={() => setView("transcript")} label="Show transcript" />
        <Chip title="Minutes" selected={view === "minutes"} onPress={() => setView("minutes")} label="Show minutes" />
        <Chip title="Preview" selected={view === "preview"} onPress={() => { void minutesSave.flush(); setView("preview"); }} label="Show preview" />
      </Chips>

      {error ? <Banner kind="error" text={error} action={gen ? undefined : "Try again"} onAction={generate} /> : null}
      {warnings.map((w) => <Banner key={w} kind="warn" text={w} />)}

      {view === "transcript" ? (
        <View style={{ gap: 10 }}>
          {!transcript.trim() && m.audioPath ? (
            <Banner text="This meeting has a recording that isn't transcribed yet." action="Transcribe" onAction={() => router.push(`/meeting/${id}/transcribe`)} />
          ) : null}
          <Field label="Transcript" value={transcript} onChangeText={setTranscript} multiline style={{ minHeight: 260 }}
            placeholder="The transcript appears here. You can edit it before generating minutes." />
          {speakers.length > 1 ? (
            <Section title="Speaker names">
              {speakers.map((s) => <Field key={s} label={s} value={names[s] ?? ""} placeholder="Name" onChangeText={(t) => setNames({ ...names, [s]: t })} />)}
              <Button kind="secondary" title="Apply names" onPress={applyNames} disabled={!Object.values(names).some((v) => v.trim())} />
            </Section>
          ) : null}
        </View>
      ) : null}

      {view === "minutes" ? (
        <View style={{ gap: 10 }}>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}><View style={{ flexDirection: "row", gap: 6 }}>
            {tool("Heading", () => edit(prefixLines(minutes, sel, "## ")))}
            {tool("Bold", () => edit(wrap(minutes, sel, "**", "bold")))}
            {tool("Italic", () => edit(wrap(minutes, sel, "*", "italic")))}
            {tool("List", () => edit(prefixLines(minutes, sel, "- ")))}
            {tool("Table", () => edit(insertBlock(minutes, sel, TABLE_TEMPLATE)))}
            {tool("Undo", undo)}
          </View></ScrollView>
          <TextInput accessibilityLabel="Minutes editor" value={minutes} onChangeText={setMinutes} multiline textAlignVertical="top"
            selection={forcedSel} onSelectionChange={(e) => { setSel(e.nativeEvent.selection); setForcedSel(undefined); }}
            placeholder="Generate minutes, or write them here in Markdown." placeholderTextColor={c.muted}
            style={[{ minHeight: 320, backgroundColor: c.input, color: c.text, borderColor: c.border, borderWidth: 1, borderRadius: radius,
              padding: 12, fontSize: 15 }, hasArabic(minutes) ? rtlStyle(minutes) : {}]} />
        </View>
      ) : null}

      {view === "preview" ? (
        <View style={{ height: 520, borderRadius: radius, overflow: "hidden", borderWidth: 1, borderColor: c.border }}>
          <WebView originWhitelist={["about:blank"]} source={{ html: previewHtml }} javaScriptEnabled={false} accessibilityLabel="Minutes preview" />
        </View>
      ) : null}

      {gen ? (
        <Card>
          <ProgressBar value={gen.value} label={gen.text} />
          <Button kind="secondary" title="Cancel" onPress={() => ctl.current?.abort()} />
        </Card>
      ) : (
        <View style={{ gap: 8 }}>
          <Button title={minutes.trim() ? "Generate again" : "Generate minutes"} label="Generate minutes" onPress={generate}
            disabled={!cloudEnabled() || !transcript.trim()} />
          {!cloudEnabled() ? <T muted size={13}>Minutes need MICO360 Cloud, which isn't enabled in this build. You can still write them yourself.</T> : null}
          <Button kind="secondary" title="Export & share" label="Export and share" disabled={!minutes.trim()}
            onPress={async () => { await minutesSave.flush(); router.push(`/meeting/${id}/export`); }} />
          <Button kind="ghost" title={m.status === "final" ? "Mark as draft" : "Mark as final"} onPress={toggleFinal} />
        </View>
      )}
    </Screen>
  );
}

export function ErrorBoundary({ error, retry }: { error: Error; retry: () => void }) {
  return <Screen><Banner kind="error" text={`Something went wrong: ${error.message}`} action="Try again" onAction={retry} /></Screen>;
}
