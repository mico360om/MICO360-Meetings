import { View } from "react-native";
import { computeInsights } from "../../core/insights";
import { listActionItems } from "../../data/actions";
import { listMeetings } from "../../data/meetings";
import { Card, Chip, Chips, Empty, Screen, Section, Spinner, T } from "../../ui/components";
import { useQuery } from "../../ui/data";
import { usePalette } from "../../ui/theme";

function Bars({ rows }: { rows: Array<[string, number]> }) {
  const c = usePalette();
  const max = Math.max(1, ...rows.map(([, n]) => n));
  return (
    <View style={{ gap: 6 }}>
      {rows.map(([k, n]) => (
        <View key={k} style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <T size={13} style={{ width: 110 }} numberOfLines={1}>{k}</T>
          <View style={{ flex: 1, height: 10, backgroundColor: c.border, borderRadius: 5, overflow: "hidden" }}>
            <View style={{ width: `${(n / max) * 100}%`, height: 10, backgroundColor: c.accent }} />
          </View>
          <T size={13} style={{ width: 28, textAlign: "right" }}>{n}</T>
        </View>
      ))}
    </View>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Card style={{ flexGrow: 1, flexBasis: "45%" }}>
      <T size={24} bold>{value}</T>
      <T muted size={13}>{label}</T>
    </Card>
  );
}

export default function Insights() {
  const { data } = useQuery(async (db) => {
    const meetings = await listMeetings(db);
    return computeInsights(meetings.map((m) => ({ id: m.id, title: m.title, createdAt: m.createdAt, minutes: m.minutes })), await listActionItems(db));
  });
  if (!data) return <Screen><Spinner /></Screen>;
  if (!data.totalMeetings) return <Screen><Empty title="No insights yet" hint="Insights appear once you have meetings with minutes." /></Screen>;
  return (
    <Screen>
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
        <Stat label="Meetings" value={String(data.totalMeetings)} />
        <Stat label="Open action items" value={String(data.openItems)} />
        <Stat label="Overdue" value={String(data.overdueItems)} />
        <Stat label="Completion rate" value={`${Math.round(data.completionRate * 100)}%`} />
      </View>
      {Object.keys(data.statusCounts).length ? <Section title="Action items by status"><Card><Bars rows={Object.entries(data.statusCounts)} /></Card></Section> : null}
      {data.byOwner.length ? <Section title="Open items by owner"><Card><Bars rows={data.byOwner} /></Card></Section> : null}
      <Section title="Meetings per week"><Card><Bars rows={data.cadence} /></Card></Section>
      {data.keywords.length ? (
        <Section title="Recurring topics">
          <Chips>{data.keywords.map(([w, n]) => <Chip key={w} title={`${w} · ${n}`} />)}</Chips>
        </Section>
      ) : null}
    </Screen>
  );
}
