import { router } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { Alert, View } from "react-native";
import { File } from "expo-file-system";
import { createMeeting, deleteMeeting, listMeetings, type Meeting } from "../../data/meetings";
import { recoverInterruptedRecordings } from "../../native/recorder";
import { syncReminders } from "../../native/device";
import { Banner, Button, Card, Chip, Chips, Empty, Field, Screen, Spinner, T } from "../../ui/components";
import { errorText, notifyChanged, useDb, useQuery } from "../../ui/data";

type Filter = "all" | "draft" | "final";

const when = (ms: number) => new Date(ms).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
const snippet = (m: Meeting) => (m.minutes || m.transcript).replace(/[#*|_>-]/g, " ").replace(/\s+/g, " ").trim().slice(0, 120);

export default function Meetings() {
  const db = useDb();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [recovered, setRecovered] = useState<string[]>([]);
  const { data, error, loading } = useQuery((d) => listMeetings(d, { search, status: filter === "all" ? undefined : filter }), [search, filter]);

  useEffect(() => {
    (async () => {
      const known = new Set((await listMeetings(db)).map((m) => m.audioPath).filter(Boolean));
      setRecovered(await recoverInterruptedRecordings(known));
    })().catch(() => undefined);
  }, [db]);

  const keepRecovered = async () => {
    for (const uri of recovered) {
      await createMeeting(db, { title: "Recovered recording", source: "record", audioPath: uri });
    }
    setRecovered([]);
    notifyChanged();
  };

  const discardRecovered = () => Alert.alert("Delete the interrupted recording?", "The audio will be deleted.", [
    { text: "Cancel", style: "cancel" },
    { text: "Delete", style: "destructive", onPress: () => {
      for (const uri of recovered) { const f = new File(uri); if (f.exists) f.delete(); }
      setRecovered([]);
    } },
  ]);

  // Delete with Undo: the meeting disappears at once and is really deleted 6 s later
  // (or when the next one is deleted, or when this screen goes away).
  const [pending, setPending] = useState<Meeting | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingRef = useRef<Meeting | null>(null);
  const commit = async (m: Meeting) => {
    try {
      await deleteMeeting(db, m.id);
      if (m.audioPath) { const f = new File(m.audioPath); if (f.exists) f.delete(); }
      notifyChanged();
      await syncReminders(db);
    } catch (e) { Alert.alert("Couldn't delete", errorText(e)); }
  };
  const flushPending = () => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    const m = pendingRef.current;
    pendingRef.current = null;
    setPending(null);
    if (m) void commit(m);
  };
  const remove = (m: Meeting) => {
    flushPending();
    pendingRef.current = m;
    setPending(m);
    timer.current = setTimeout(flushPending, 6000);
  };
  const undo = () => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    pendingRef.current = null;
    setPending(null);
  };
  useEffect(() => () => flushPending(), []);   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <View style={{ flex: 1 }}>
      <Screen>
        {recovered.length ? <Banner kind="warn" text={`${recovered.length} recording${recovered.length > 1 ? "s were" : " was"} interrupted. The audio was kept.`}
          action="Keep as draft" onAction={keepRecovered} /> : null}
        {recovered.length ? <Button small kind="ghost" title="Delete it instead" label="Delete interrupted recording" onPress={discardRecovered} /> : null}
        <Field label="Search meetings" value={search} onChangeText={setSearch} placeholder="Title, attendees, words in the minutes" />
        <Chips>
          {(["all", "draft", "final"] as Filter[]).map((f) => (
            <Chip key={f} title={f === "all" ? "All" : f === "draft" ? "Drafts" : "Final"} selected={filter === f} onPress={() => setFilter(f)} />
          ))}
        </Chips>
        {error ? <Banner kind="error" text={error} /> : null}
        {loading && !data ? <Spinner /> : null}
        {data && !data.length ? <Empty title={search ? "No meetings match" : "No meetings yet"} hint={search ? undefined : "Tap New meeting to record, import a file or paste a transcript."} /> : null}
        {pending ? <Banner kind="info" text={`"${pending.title || "Untitled meeting"}" was deleted.`} action="Undo" onAction={undo} /> : null}
        {data?.filter((m) => m.id !== pending?.id).map((m) => (
          <Card key={m.id} label={`Open meeting ${m.title || "Untitled meeting"}`} onPress={() => router.push(`/meeting/${m.id}`)}>
            <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 8 }}>
              <T bold style={{ flex: 1 }} numberOfLines={1}>{m.title || "Untitled meeting"}</T>
              <T muted size={12}>{m.status === "final" ? "Final" : "Draft"}</T>
            </View>
            <T muted size={13}>{[m.meetingDate || when(m.createdAt), m.attendees].filter(Boolean).join(" · ")}</T>
            {snippet(m) ? <T size={13} numberOfLines={2}>{snippet(m)}</T> : null}
            <View style={{ alignSelf: "flex-end" }}><Button small kind="ghost" title="Delete" label={`Delete meeting ${m.title || "Untitled meeting"}`} onPress={() => remove(m)} /></View>
          </Card>
        ))}
      </Screen>
      <View style={{ position: "absolute", right: 16, bottom: 16 }}>
        <Button title="+ New meeting" label="New meeting" onPress={() => router.push("/meeting/new")} />
      </View>
    </View>
  );
}
