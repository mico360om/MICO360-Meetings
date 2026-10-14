import { Stack } from "expo-router";
import { useState } from "react";
import { nativeExportDeps, shareFile } from "../../native/exportFiles";
import { logoStore, pickTextFile } from "../../native/libraryFiles";
import { exportProfiles, exportPrompts, importProfiles, importPrompts } from "../../services/library";
import { Banner, Button, Card, Screen, Section, T } from "../../ui/components";
import { errorText, notifyChanged, useDb } from "../../ui/data";

export default function Transfer() {
  const db = useDb();
  const [message, setMessage] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState("");

  const run = async (key: string, f: () => Promise<string | null>) => {
    setBusy(key); setMessage(null);
    try { const text = await f(); if (text) setMessage({ kind: "success", text }); notifyChanged(); }
    catch (e) { setMessage({ kind: "error", text: errorText(e) }); }
    finally { setBusy(""); }
  };

  const share = async (name: string, json: string) => {
    const uri = await nativeExportDeps.writeText(name, json);
    await shareFile(uri, "application/json", `Share ${name}`);
    return null;
  };

  return (
    <Screen>
      <Stack.Screen options={{ title: "Import & export" }} />
      <T muted size={13}>Files use the desktop app's formats, so prompts and companies move between MICO360 Meetings on the computer and on the phone. Importing never overwrites: a different item with a taken name gets a new name, and identical items are skipped.</T>
      {message ? <Banner kind={message.kind} text={message.text} /> : null}
      <Section title="Prompts">
        <Card>
          <Button title="Import prompts" busy={busy === "ip"} onPress={() => run("ip", async () => {
            const f = await pickTextFile(["application/json", "text/plain", "text/markdown", "*/*"]);
            return f ? (await importPrompts(db, f.name, f.text)).message : null;
          })} />
          <Button kind="secondary" title="Export all prompts" busy={busy === "ep"} onPress={() => run("ep", async () => share("MICO360-Prompts.json", await exportPrompts(db, [])))} />
        </Card>
      </Section>
      <Section title="Companies">
        <Card>
          <Button title="Import companies" busy={busy === "ic"} onPress={() => run("ic", async () => {
            const f = await pickTextFile(["application/json", "text/csv", "text/comma-separated-values", "*/*"]);
            return f ? (await importProfiles(db, f.name, f.text, logoStore)).message : null;
          })} />
          <Button kind="secondary" title="Export all companies" busy={busy === "ec"} onPress={() => run("ec", async () => share("MICO360-Companies.json", await exportProfiles(db, [], logoStore)))} />
        </Card>
      </Section>
    </Screen>
  );
}
