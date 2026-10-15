import { router } from "expo-router";
import { useState } from "react";
import { Pressable, View } from "react-native";
import { type ActionItem, effectiveStatus, isOverdue, PRIORITIES, STATUS_CYCLE } from "../../core/actionItems";
import { listActionItems, resetItem, saveItemEdits } from "../../data/actions";
import { syncReminders } from "../../native/device";
import { nativeExportDeps, shareFile } from "../../native/exportFiles";
import { exportActionItems } from "../../services/exports";
import { Banner, Button, Card, Chip, Chips, Empty, Field, Screen, Spinner, T } from "../../ui/components";
import { errorText, notifyChanged, useDb, useQuery } from "../../ui/data";
import { usePalette } from "../../ui/theme";

type Filter = "open" | "overdue" | "done" | "all";

function Editor({ item, onClose }: { item: ActionItem; onClose: () => void }) {
  const db = useDb();
  const [v, setV] = useState({ task: item.task, owner: item.owner, deadline: item.deadline, status: item.status, priority: item.priority, notes: item.notes });
  const [error, setError] = useState("");
  const done = async (f: () => Promise<void>) => {
    try { await f(); notifyChanged(); await syncReminders(db); onClose(); } catch (e) { setError(errorText(e)); }
  };
  return (
    <View style={{ gap: 10, marginTop: 8 }}>
      <Field label="Task" value={v.task} onChangeText={(t) => setV({ ...v, task: t })} multiline style={{ minHeight: 60 }} />
      <Field label="Owner" value={v.owner} onChangeText={(t) => setV({ ...v, owner: t })} />
      <Field label="Deadline" value={v.deadline} onChangeText={(t) => setV({ ...v, deadline: t })} placeholder="e.g. 2026-10-30 or 30 Oct" />
      <Chips>{STATUS_CYCLE.map((s) => <Chip key={s} title={s} selected={v.status === s} onPress={() => setV({ ...v, status: s })} label={`Status ${s}`} />)}</Chips>
      <Chips>
        {["", ...PRIORITIES].map((p) => <Chip key={p || "none"} title={p || "No priority"} selected={v.priority === p} onPress={() => setV({ ...v, priority: p })} />)}
      </Chips>
      <Field label="Notes" value={v.notes} onChangeText={(t) => setV({ ...v, notes: t })} multiline style={{ minHeight: 60 }} />
      {error ? <Banner kind="error" text={error} /> : null}
      <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
        <Button title="Save" label="Save action item" onPress={() => done(() => saveItemEdits(db, item, v))} />
        <Button kind="secondary" title="Cancel" onPress={onClose} />
        <Button kind="ghost" title="Reset to minutes" onPress={() => done(() => resetItem(db, item))} />
      </View>
    </View>
  );
}

export default function ActionItems() {
  const db = useDb();
  const c = usePalette();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("open");
  const [open, setOpen] = useState<string | null>(null);
  const [owner, setOwner] = useState("");
  const [error, setError] = useState("");
  const { data, loading } = useQuery((d) => listActionItems(d, search), [search]);

  const owners = [...new Set((data ?? []).map((i) => i.owner.trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b)).slice(0, 12);
  const shown = (data ?? []).filter((i) => {
    if (owner && i.owner.trim() !== owner) return false;
    const s = effectiveStatus(i);
    if (filter === "open") return s !== "Completed" && s !== "Cancelled";
    if (filter === "overdue") return isOverdue(i);
    if (filter === "done") return s === "Completed";
    return true;
  });

  const share = async (kind: "csv" | "ics") => {
    setError("");
    try {
      const r = await exportActionItems(shown, kind, nativeExportDeps);
      await shareFile(r.uri, r.mime, `Share ${r.name}`);
    } catch (e) { setError(errorText(e)); }
  };

  const color = (s: string) => (s === "Overdue" ? c.danger : s === "Completed" ? c.success : s === "In Progress" ? c.info : c.muted);
  return (
    <Screen>
      <Field label="Search action items" value={search} onChangeText={setSearch} placeholder="Task, owner, meeting" />
      <Chips>
        {([["open", "Open"], ["overdue", "Overdue"], ["done", "Completed"], ["all", "All"]] as Array<[Filter, string]>).map(([f, t]) =>
          <Chip key={f} title={t} selected={filter === f} onPress={() => setFilter(f)} label={`Filter ${t}`} />)}
      </Chips>
      {owners.length > 1 ? (
        <Chips>
          <Chip title="Everyone" selected={!owner} onPress={() => setOwner("")} label="Owner everyone" />
          {owners.map((o) => <Chip key={o} title={o} selected={owner === o} onPress={() => setOwner(owner === o ? "" : o)} label={`Owner ${o}`} />)}
        </Chips>
      ) : null}
      {error ? <Banner kind="error" text={error} /> : null}
      {loading && !data ? <Spinner /> : null}
      {data && !shown.length ? <Empty title="No action items here" hint="Action items come from the Action Items table in your minutes." /> : null}
      {shown.map((i) => {
        const s = effectiveStatus(i);
        return (
          <Card key={i.okey} label={`Action item ${i.task}`} onPress={open === i.okey ? undefined : () => setOpen(i.okey)}>
            <T bold>{i.task}</T>
            <T muted size={13}>{[i.owner, i.deadline, i.priority].filter(Boolean).join(" · ")}</T>
            <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
              <T size={13} style={{ color: color(s) }}>{s}</T>
              <Pressable accessibilityRole="link" accessibilityLabel={`Open meeting ${i.meetingTitle || "Meeting"}`} hitSlop={8}
                onPress={() => router.push(`/meeting/${i.meetingId}`)}>
                <T muted size={12}>{i.meetingTitle || "Meeting"} ›</T>
              </Pressable>
            </View>
            {i.notes ? <T size={13}>{i.notes}</T> : null}
            {open === i.okey ? <Editor item={i} onClose={() => setOpen(null)} /> : null}
          </Card>
        );
      })}
      {shown.length ? (
        <View style={{ flexDirection: "row", gap: 8 }}>
          <Button kind="secondary" title="Export CSV" onPress={() => share("csv")} />
          <Button kind="secondary" title="Add to calendar" label="Export calendar" onPress={() => share("ics")} />
        </View>
      ) : null}
    </Screen>
  );
}
