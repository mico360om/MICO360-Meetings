import * as ImagePicker from "expo-image-picker";
import { File } from "expo-file-system";
import { router, Stack, useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";
import { Image, Switch, View } from "react-native";
import { DESIGNS } from "../../core/designs";
import { type CompanyProfile, DEFAULT_PROFILE } from "../../core/profiles";
import { deleteProfile, getProfile, saveProfile } from "../../data/library";
import { logoStore } from "../../native/libraryFiles";
import { duplicateProfile } from "../../services/library";
import { Banner, Button, Card, Chip, Chips, Field, Screen, Section, Spinner, T } from "../../ui/components";
import { errorText, notifyChanged, useDb } from "../../ui/data";
import { confirm } from "../../ui/hooks";
import { usePalette } from "../../ui/theme";

const ALIGN = ["left", "center", "right"];
const PAGE_POS = ["footer-left", "footer-center", "footer-right", "header-left", "header-center", "header-right"];

export default function CompanyEditor() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const db = useDb();
  const c = usePalette();
  const [p, setP] = useState<CompanyProfile | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!id) { setP({ ...DEFAULT_PROFILE, id: "", name: "" }); return; }
    getProfile(db, id).then((x) => setP(x ?? { ...DEFAULT_PROFILE, id: "", name: "" })).catch((e) => setError(errorText(e)));
  }, [db, id]);
  if (!p) return <Screen><Spinner /></Screen>;

  const set = <K extends keyof CompanyProfile>(k: K, v: CompanyProfile[K]) => setP({ ...p, [k]: v });

  const pickLogo = async () => {
    try {
      const r = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], quality: 1 });
      if (r.canceled || !r.assets?.length) return;
      const a = r.assets[0];
      const ext = (a.fileName?.split(".").pop() ?? a.mimeType?.split("/").pop() ?? "png").toLowerCase();
      const uri = await logoStore.save(await new File(a.uri).base64(), ["png", "jpg", "jpeg", "gif", "bmp", "webp"].includes(ext) ? ext : "png");
      set("logo_path", uri);
    } catch (e) { setError(errorText(e)); }
  };

  const save = async () => {
    if (!p.name.trim()) { setError("Give the company a name."); return; }
    setBusy(true);
    try { await saveProfile(db, p); notifyChanged(); router.back(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };

  const remove = async () => {
    if (!(await confirm(`Delete ${p.name}?`, "Meetings that use it will be exported without branding.", "Delete", true))) return;
    await deleteProfile(db, p.id);
    if (p.logo_path) await logoStore.remove(p.logo_path);
    notifyChanged();
    router.back();
  };

  const duplicate = async () => {
    const copy = await duplicateProfile(db, p.id, logoStore);
    notifyChanged();
    if (copy) router.replace({ pathname: "/settings/company", params: { id: copy.id } });
  };

  return (
    <Screen>
      <Stack.Screen options={{ title: p.id ? p.name || "Company" : "New company" }} />
      {error ? <Banner kind="error" text={error} /> : null}
      <Field label="Company name" value={p.name} onChangeText={(t) => set("name", t)} />
      <Field label="Address" value={p.address} onChangeText={(t) => set("address", t)} multiline style={{ minHeight: 60 }} />
      <Field label="Phone" value={p.phone} onChangeText={(t) => set("phone", t)} keyboardType="phone-pad" />
      <Field label="E-mail" value={p.email} onChangeText={(t) => set("email", t)} keyboardType="email-address" autoCapitalize="none" />
      <Field label="Website" value={p.website} onChangeText={(t) => set("website", t)} autoCapitalize="none" />
      <Section title="Logo">
        <Card>
          {p.logo_path ? <Image source={{ uri: p.logo_path }} style={{ width: 160, height: 64, resizeMode: "contain" }} accessibilityLabel="Company logo" /> : <T muted>No logo</T>}
          <View style={{ flexDirection: "row", gap: 8, marginTop: 8 }}>
            <Button small kind="secondary" title={p.logo_path ? "Change logo" : "Choose logo"} onPress={pickLogo} />
            {p.logo_path ? <Button small kind="ghost" title="Remove" onPress={() => set("logo_path", "")} /> : null}
          </View>
          <Chips>{ALIGN.map((a) => <Chip key={a} title={`Logo ${a}`} selected={p.logo_position === a} onPress={() => set("logo_position", a)} />)}</Chips>
        </Card>
      </Section>
      <Section title="PDF design">
        <Chips>{DESIGNS.map((d) => <Chip key={d.key} title={d.name} selected={p.pdf_design === d.key} onPress={() => set("pdf_design", d.key)} label={`Design ${d.name}`} />)}</Chips>
        <T muted size={13}>{DESIGNS.find((d) => d.key === p.pdf_design)?.blurb ?? ""}</T>
        <Field label="Accent colour (e.g. #8B1E1E)" value={p.accent_color} onChangeText={(t) => set("accent_color", t)} autoCapitalize="none" />
      </Section>
      <Section title="Footer">
        <Field label="Footer text" value={p.footer_text} onChangeText={(t) => set("footer_text", t)} />
        <Chips>{ALIGN.map((a) => <Chip key={a} title={a} selected={p.footer_alignment === a} onPress={() => set("footer_alignment", a)} label={`Footer ${a}`} />)}</Chips>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <Switch value={p.show_page_numbers} onValueChange={(v) => set("show_page_numbers", v)} trackColor={{ true: c.accent }} accessibilityLabel="Page numbers" />
          <T>Page numbers</T>
        </View>
        {p.show_page_numbers ? (
          <>
            <Chips>{PAGE_POS.map((a) => <Chip key={a} title={a} selected={p.page_number_position === a} onPress={() => set("page_number_position", a)} />)}</Chips>
            <Field label="Page number format" value={p.page_number_format} onChangeText={(t) => set("page_number_format", t)} placeholder="Page {n} of {total}" />
          </>
        ) : null}
      </Section>
      <Button title="Save" label="Save company" busy={busy} onPress={save} />
      {p.id ? (
        <View style={{ flexDirection: "row", gap: 8 }}>
          <Button kind="secondary" title="Duplicate" onPress={duplicate} />
          <Button kind="ghost" title="Delete" onPress={remove} />
        </View>
      ) : null}
    </Screen>
  );
}
