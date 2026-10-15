import { useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";
import { View } from "react-native";
import { DESIGNS } from "../../../core/designs";
import { listProfiles } from "../../../data/library";
import { getMeeting, type Meeting } from "../../../data/meetings";
import { saveToFolder, nativeExportDeps, shareFile } from "../../../native/exportFiles";
import { type ExportFormat, exportMinutes } from "../../../services/exports";
import { Banner, Button, Card, Chip, Chips, Screen, Section, Spinner, T } from "../../../ui/components";
import { errorText, useDb, useQuery } from "../../../ui/data";

const FORMATS: Array<[ExportFormat, string]> = [["pdf", "PDF"], ["docx", "Word"], ["txt", "Text"], ["md", "Markdown"]];

export default function ExportScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const db = useDb();
  const [m, setM] = useState<Meeting | null>(null);
  const [format, setFormat] = useState<ExportFormat>("pdf");
  const [profileId, setProfileId] = useState<string | null>(null);
  const [design, setDesign] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const profiles = useQuery(listProfiles).data ?? [];

  useEffect(() => { getMeeting(db, id).then((x) => { setM(x); setProfileId(x?.profileId ?? ""); }).catch((e) => setError(errorText(e))); }, [db, id]);
  if (!m || profileId === null) return <Screen>{error ? <Banner kind="error" text={error} /> : <Spinner />}</Screen>;

  const profile = profiles.find((p) => p.id === profileId) ?? null;
  const designKey = design ?? profile?.pdf_design ?? DESIGNS[0].key;

  const run = async (to: "share" | "files") => {
    setBusy(true); setError(""); setDone("");
    try {
      const r = await exportMinutes(m, profile, format, nativeExportDeps, format === "pdf" ? designKey : null);
      if (to === "share") { await shareFile(r.uri, r.mime, `Share ${r.name}`); setDone(`${r.name} is ready.`); }
      else { const where = await saveToFolder(r.uri, r.name, r.mime); if (where) setDone(`Saved ${r.name} to ${where}.`); }
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };

  return (
    <Screen>
      <Section title="Format">
        <Chips>{FORMATS.map(([f, label]) => <Chip key={f} title={label} selected={format === f} onPress={() => setFormat(f)} label={`Format ${label}`} />)}</Chips>
      </Section>
      <Section title="Company branding">
        <Chips>
          <Chip title="No branding" selected={!profileId} onPress={() => setProfileId("")} />
          {profiles.map((p) => <Chip key={p.id} title={p.name} selected={profileId === p.id} onPress={() => { setProfileId(p.id); setDesign(null); }} />)}
        </Chips>
      </Section>
      {format === "pdf" ? (
        <Section title="PDF design">
          {DESIGNS.map((d) => (
            <Card key={d.key} label={`Design ${d.name}`} onPress={() => setDesign(d.key)}
              style={designKey === d.key ? { borderWidth: 2 } : undefined}>
              <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
                <T bold>{d.name}</T>
                {designKey === d.key ? <T muted size={13}>Selected</T> : null}
              </View>
              <T muted size={13}>{d.blurb}</T>
            </Card>
          ))}
        </Section>
      ) : null}
      {error ? <Banner kind="error" text={error} /> : null}
      {done ? <Banner kind="success" text={done} /> : null}
      <Button title="Share" label="Share export" busy={busy} onPress={() => run("share")} />
      <Button kind="secondary" title="Save to files" label="Save export to files" disabled={busy} onPress={() => run("files")} />
    </Screen>
  );
}
