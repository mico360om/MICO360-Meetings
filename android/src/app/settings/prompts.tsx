import { router, Stack } from "expo-router";
import { isModified } from "../../core/prompts";
import { listPrompts } from "../../data/library";
import { Button, Card, Row, Screen, Section, Spinner } from "../../ui/components";
import { useQuery } from "../../ui/data";

export default function Prompts() {
  const { data } = useQuery(listPrompts);
  if (!data) return <Screen><Spinner /></Screen>;
  const favourites = data.filter((p) => p.favorite);
  const byCategory = new Map<string, typeof data>();
  for (const p of data) byCategory.set(p.category, [...(byCategory.get(p.category) ?? []), p]);
  const row = (p: (typeof data)[number]) => (
    <Row key={p.id} title={`${p.favorite ? "★ " : ""}${p.name}`} label={`Prompt ${p.name}`}
      detail={p.builtin ? (isModified(p) ? "Built-in · edited" : "Built-in") : "Your prompt"}
      onPress={() => router.push({ pathname: "/settings/prompt", params: { id: p.id } })} />
  );
  return (
    <Screen>
      <Stack.Screen options={{ title: "Prompts" }} />
      <Button title="Add prompt" onPress={() => router.push({ pathname: "/settings/prompt", params: { id: "" } })} />
      {favourites.length ? <Section title="Favourites"><Card>{favourites.map(row)}</Card></Section> : null}
      {[...byCategory].map(([cat, list]) => <Section key={cat} title={cat}><Card>{list.map(row)}</Card></Section>)}
    </Screen>
  );
}
