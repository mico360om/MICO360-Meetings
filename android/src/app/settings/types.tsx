import { Stack } from "expo-router";
import { useState } from "react";
import { ScrollView, View } from "react-native";
import type { MeetingType } from "../../core/meetingTypes";
import { STYLE_NAMES } from "../../core/prompts";
import { deleteMeetingType, listMeetingTypes, listProfiles, listPrompts, saveMeetingType } from "../../data/library";
import { Banner, Button, Card, Chip, Chips, Field, Screen, Section, Spinner, T } from "../../ui/components";
import { errorText, notifyChanged, useDb, useQuery } from "../../ui/data";

const BLANK: MeetingType = { name: "", prompt_name: "", style: "", profile_name: "" };

export default function MeetingTypes() {
  const db = useDb();
  const { data } = useQuery(listMeetingTypes);
  const prompts = useQuery(listPrompts).data ?? [];
  const profiles = useQuery(listProfiles).data ?? [];
  const [editing, setEditing] = useState<{ original: string; t: MeetingType } | null>(null);
  const [error, setError] = useState("");
  if (!data) return <Screen><Spinner /></Screen>;

  const save = async () => {
    if (!editing) return;
    setError("");
    try {
      if (editing.original && editing.original !== editing.t.name.trim()) await deleteMeetingType(db, editing.original);
      await saveMeetingType(db, editing.t);
      setEditing(null);
      notifyChanged();
    } catch (e) { setError(errorText(e)); }
  };

  const form = editing ? (
    <Card>
      <Field label="Type name" value={editing.t.name} onChangeText={(t) => setEditing({ ...editing, t: { ...editing.t, name: t } })} />
      <Section title="Template">
        <ScrollView horizontal showsHorizontalScrollIndicator={false}><View style={{ flexDirection: "row", gap: 8 }}>
          {prompts.map((p) => <Chip key={p.id} title={p.name} selected={editing.t.prompt_name === p.name} onPress={() => setEditing({ ...editing, t: { ...editing.t, prompt_name: p.name } })} />)}
        </View></ScrollView>
      </Section>
      <Section title="Style">
        <Chips>
          <Chip title="Prompt's own" selected={!editing.t.style} onPress={() => setEditing({ ...editing, t: { ...editing.t, style: "" } })} />
          {STYLE_NAMES.map((s) => <Chip key={s} title={s} selected={editing.t.style === s} onPress={() => setEditing({ ...editing, t: { ...editing.t, style: s } })} />)}
        </Chips>
      </Section>
      <Section title="Company">
        <Chips>
          <Chip title="None" selected={!editing.t.profile_name} onPress={() => setEditing({ ...editing, t: { ...editing.t, profile_name: "" } })} />
          {profiles.map((p) => <Chip key={p.id} title={p.name} selected={editing.t.profile_name === p.name} onPress={() => setEditing({ ...editing, t: { ...editing.t, profile_name: p.name } })} />)}
        </Chips>
      </Section>
      {error ? <Banner kind="error" text={error} /> : null}
      <View style={{ flexDirection: "row", gap: 8 }}>
        <Button title="Save" label="Save meeting type" onPress={save} />
        <Button kind="secondary" title="Cancel" onPress={() => setEditing(null)} />
      </View>
    </Card>
  ) : null;

  return (
    <Screen>
      <Stack.Screen options={{ title: "Meeting types" }} />
      <T muted size={13}>Choosing a type when creating a meeting sets its template, style and company.</T>
      {editing && !editing.original ? form : <Button title="Add meeting type" onPress={() => setEditing({ original: "", t: { ...BLANK } })} />}
      {data.map((t) => editing?.original === t.name ? <View key={t.name}>{form}</View> : (
        <Card key={t.name} label={`Meeting type ${t.name}`} onPress={() => setEditing({ original: t.name, t: { ...t } })}>
          <T bold>{t.name}</T>
          <T muted size={13}>{[t.prompt_name, t.style, t.profile_name].filter(Boolean).join(" · ") || "Defaults"}</T>
          <View style={{ alignSelf: "flex-end" }}>
            <Button small kind="ghost" title="Delete" label={`Delete meeting type ${t.name}`} onPress={async () => { await deleteMeetingType(db, t.name); notifyChanged(); }} />
          </View>
        </Card>
      ))}
    </Screen>
  );
}
