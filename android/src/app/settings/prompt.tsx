import { router, Stack, useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";
import { View } from "react-native";
import { isModified, type SavedPrompt, TRANSCRIPT_TOKEN } from "../../core/prompts";
import { addPrompt, deletePrompt, getPrompt, listPrompts, restorePrompt, setPromptFavorite, updatePrompt } from "../../data/library";
import { Banner, Button, Field, Screen, Spinner, T } from "../../ui/components";
import { errorText, notifyChanged, useDb } from "../../ui/data";
import { confirm } from "../../ui/hooks";

export default function PromptEditor() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const db = useDb();
  const [p, setP] = useState<SavedPrompt | null>(null);
  const [v, setV] = useState({ name: "", category: "General", text: "" });
  const [error, setError] = useState("");

  useEffect(() => {
    if (!id) { setP({ id: "", name: "", category: "General", text: `Write minutes for this meeting.\n\n${TRANSCRIPT_TOKEN}`, builtin: false, favorite: false }); return; }
    getPrompt(db, id).then((x) => setP(x)).catch((e) => setError(errorText(e)));
  }, [db, id]);
  useEffect(() => { if (p) setV({ name: p.name, category: p.category, text: p.text }); }, [p]);
  if (!p) return <Screen>{error ? <Banner kind="error" text={error} /> : <Spinner />}</Screen>;

  const save = async () => {
    setError("");
    const name = v.name.trim();
    if (!name || !v.text.trim()) { setError("A prompt needs a name and some text."); return; }
    const clash = (await listPrompts(db)).find((x) => x.name.toLowerCase() === name.toLowerCase() && x.id !== p.id);
    if (clash) { setError(`There is already a prompt called "${clash.name}".`); return; }
    try {
      if (p.id) await updatePrompt(db, p.id, v); else await addPrompt(db, v);
      notifyChanged();
      router.back();
    } catch (e) { setError(errorText(e)); }
  };

  const act = async (f: () => Promise<unknown>) => { try { await f(); notifyChanged(); const x = p.id ? await getPrompt(db, p.id) : null; if (x) setP(x); } catch (e) { setError(errorText(e)); } };

  return (
    <Screen>
      <Stack.Screen options={{ title: p.id ? p.name : "New prompt" }} />
      {error ? <Banner kind="error" text={error} /> : null}
      {p.builtin ? <T muted size={13}>Built-in prompt{isModified(p) ? " (edited)" : ""}. Renaming it makes it your own copy.</T> : null}
      <Field label="Prompt name" value={v.name} onChangeText={(t) => setV({ ...v, name: t })} />
      <Field label="Category" value={v.category} onChangeText={(t) => setV({ ...v, category: t })} />
      <Field label="Prompt text" value={v.text} onChangeText={(t) => setV({ ...v, text: t })} multiline style={{ minHeight: 280 }} />
      <T muted size={13}>{TRANSCRIPT_TOKEN} marks where the transcript goes; without it the transcript is added at the end.</T>
      <Button title="Save" label="Save prompt" onPress={save} />
      {p.id ? (
        <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
          <Button kind="secondary" title={p.favorite ? "Remove favourite" : "Add to favourites"} onPress={() => act(() => setPromptFavorite(db, p.id, !p.favorite))} />
          {p.builtin && isModified(p) ? <Button kind="secondary" title="Restore built-in text" onPress={() => act(() => restorePrompt(db, p.id))} /> : null}
          <Button kind="ghost" title="Delete" onPress={async () => {
            if (!(await confirm(`Delete "${p.name}"?`, p.builtin ? "Built-in prompts come back the next time the app starts." : "This can't be undone.", "Delete", true))) return;
            await deletePrompt(db, p.id); notifyChanged(); router.back();
          }} />
        </View>
      ) : null}
    </Screen>
  );
}
