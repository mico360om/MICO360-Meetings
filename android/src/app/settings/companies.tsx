import { router, Stack } from "expo-router";
import { listProfiles } from "../../data/library";
import { Button, Card, Empty, Row, Screen, Spinner } from "../../ui/components";
import { useQuery } from "../../ui/data";

export default function Companies() {
  const { data } = useQuery(listProfiles);
  return (
    <Screen>
      <Stack.Screen options={{ title: "Companies" }} />
      {!data ? <Spinner /> : !data.length ? <Empty title="No companies yet" hint="A company adds its letterhead, logo and PDF design to exported minutes." /> : (
        <Card>
          {data.map((p) => <Row key={p.id} title={p.name} detail={[p.pdf_design, p.email || p.phone].filter(Boolean).join(" · ")}
            onPress={() => router.push({ pathname: "/settings/company", params: { id: p.id } })} label={`Company ${p.name}`} />)}
        </Card>
      )}
      <Button title="Add company" onPress={() => router.push({ pathname: "/settings/company", params: { id: "" } })} />
      <Button kind="ghost" title="Import from the desktop app" onPress={() => router.push("/settings/transfer")} />
    </Screen>
  );
}
