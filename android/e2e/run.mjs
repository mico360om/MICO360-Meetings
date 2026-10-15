// Emulator end-to-end flows with adb + uiautomator (no Maestro needed).
//   node e2e/run.mjs <adb serial> <apk>
// Needs: the fake server running (node e2e/fakeConnect.mjs) and an APK built with
//   MICO360_E2E_CONNECT_URL=http://10.0.2.2:8787/v1 and a test key in MICO360_MOBILE_CONNECT_KEY.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const [serial, apk] = process.argv.slice(2);
const ADB = process.env.ADB ?? `${process.env.LOCALAPPDATA}/Android/Sdk/platform-tools/adb.exe`;
const PKG = "com.mico360.meetings";
const adb = (...a) => execFileSync(ADB, ["-s", serial, ...a], { encoding: "utf8", maxBuffer: 1 << 24 });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;

function dump() {
  try { return adb("exec-out", "uiautomator", "dump", "/dev/tty"); } catch { return ""; }
}
function nodes(xml) {
  return [...xml.matchAll(/<node [^>]*>/g)].map(([n]) => ({
    text: /text="([^"]*)"/.exec(n)?.[1] ?? "", desc: /content-desc="([^"]*)"/.exec(n)?.[1] ?? "",
    cls: /class="([^"]*)"/.exec(n)?.[1] ?? "",
    bounds: /bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"/.exec(n)?.slice(1).map(Number) ?? [0, 0, 0, 0],
  }));
}
const unescape = (s) => s.replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&#10;/g, "\n");
const matches = (n, q) => [n.text, n.desc].map(unescape).some((v) => (q instanceof RegExp ? q.test(v) : v === q));

async function find(q, timeout = 20000, only = () => true) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const hit = nodes(dump()).find((n) => matches(n, q) && n.bounds[2] > n.bounds[0] && only(n));
    if (hit) return hit;
    await sleep(700);
  }
  throw new Error(`not found: ${q}`);
}
async function tap(q, timeout, only) {
  const n = await find(q, timeout, only);
  const [x1, y1, x2, y2] = n.bounds;
  adb("shell", "input", "tap", String(Math.round((x1 + x2) / 2)), String(Math.round((y1 + y2) / 2)));
  await sleep(600);
}
async function hideKeyboard() {
  if (/mInputShown=true/.test(adb("shell", "dumpsys", "input_method"))) { adb("shell", "input", "keyevent", "KEYCODE_BACK"); await sleep(500); }
}
async function type(q, text) {
  await hideKeyboard();                     // an open keyboard can cover the next field
  await scrollTo(q);
  for (let attempt = 0; attempt < 2; attempt++) {
    await tap(q, 20000, (n) => n.cls === "android.widget.EditText");   // the box, not its visible label
    adb("shell", "input", "text", text.replace(/ /g, "%s"));
    await sleep(400);
    if (nodes(dump()).some((n) => n.cls === "android.widget.EditText" && unescape(n.text).includes(text))) return;
  }
  throw new Error(`typing "${text}" into ${q} did not arrive`);
}
async function scrollTo(q, tries = 6) {
  for (let i = 0; i < tries; i++) {
    if (nodes(dump()).some((n) => matches(n, q))) return;
    adb("shell", "input", "swipe", "540", "1700", "540", "700", "300");
    await sleep(500);
  }
}
async function flow(name, f) {
  try { await f(); console.log(`PASS ${name}`); }
  catch (e) {
    failures++;
    const seen = nodes(dump()).map((n) => unescape(n.text || n.desc)).filter(Boolean).slice(0, 40);
    console.log(`FAIL ${name}: ${e.message}
  on screen: ${JSON.stringify(seen)}`);
  }
}

await fetch("http://127.0.0.1:8787/__reset").catch(() => { throw new Error("start the fake server first: node e2e/fakeConnect.mjs"); });
if (apk) {
  try { adb("uninstall", PKG); } catch { /* not installed yet */ }
  adb("install", "-r", "-g", apk);
}
adb("shell", "pm", "grant", PKG, "android.permission.POST_NOTIFICATIONS");
adb("shell", "am", "start", "-n", `${PKG}/.MainActivity`);

await flow("app opens on Meetings with four tabs", async () => {
  for (const t of ["Meetings tab", "Action Items tab", "Insights tab", "Settings tab"]) await find(new RegExp(`^${t}`));
  await find("No meetings yet");
});

await flow("paste → generate (redirect refused, then retry) → minutes", async () => {
  await tap("New meeting");
  await tap("Paste a transcript");
  await type("Meeting title", "E2E Budget review");
  await type("Transcript", "Ali agreed to send the budget report by January");
  await hideKeyboard();
  await scrollTo("Start meeting");
  await tap("Start meeting");
  await tap("Generate minutes");
  await tap(/^(CONTINUE|Continue)$/);               // unencrypted-connection consent
  await find(/redirected the request to another address/, 30000);
  await tap("Try again");
  await find(/Send budget report/, 30000);
});

await flow("export PDF opens the share sheet", async () => {
  await scrollTo("Export and share");
  await tap("Export and share");
  await tap("Share export");
  const end = Date.now() + 30000;
  let ok = false;
  while (Date.now() < end && !ok) {
    ok = /intentresolver|ChooserActivity|ResolverActivity/.test(adb("shell", "dumpsys", "window", "displays"));
    if (!ok) await sleep(1000);
  }
  if (!ok) throw new Error("share sheet did not open");
  adb("shell", "input", "keyevent", "KEYCODE_BACK");
  await sleep(800);
});

await flow("the action item appears on the Action Items tab", async () => {
  adb("shell", "am", "start", "-n", `${PKG}/.MainActivity`);
  await sleep(800);
  for (let i = 0; i < 3 && !nodes(dump()).some((n) => /^Action Items tab/.test(n.desc)); i++) { adb("shell", "input", "keyevent", "KEYCODE_BACK"); await sleep(600); }
  await tap(/^Action Items tab/);
  await find("Send budget report");
  await tap(/^Insights tab/);
  await find("Meetings");
});

await flow("a company can be added in Settings", async () => {
  await tap(/^Settings tab/);
  await tap("Companies");
  await tap("Add company");
  await type("Company name", "E2E Trading");
  await hideKeyboard();
  await scrollTo("Save company", 10);
  await tap("Save company");
  await find("Company E2E Trading");
});

if (process.argv.includes("--device")) {
  // Whisper + recorder on the device: downloads the 32 MB tiny model from Hugging Face.
  await flow("download the tiny speech model (verified by SHA256)", async () => {
    adb("shell", "am", "start", "-n", `${PKG}/.MainActivity`);
    for (let i = 0; i < 3 && !nodes(dump()).some((n) => /^Settings tab/.test(n.desc)); i++) { adb("shell", "input", "keyevent", "KEYCODE_BACK"); await sleep(600); }
    await tap(/^Settings tab/);
    await tap("Transcription");
    await tap("Download tiny model");
    await find(/Downloaded · in use/, 240000);
    await tap("Navigate up");
  });
  await flow("record → stop → transcribe on the device", async () => {
    adb("shell", "pm", "grant", PKG, "android.permission.RECORD_AUDIO");
    await tap(/^Meetings tab/);
    await tap("New meeting");
    await tap("Record a meeting");
    await type("Meeting title", "E2E Recording");
    await hideKeyboard();
    await scrollTo("Start meeting");
    await tap("Start meeting");
    // The recording screen never goes idle (timer, level), so uiautomator can't read it:
    // let it record, then tap "Stop and transcribe" by its place on screen.
    await sleep(15000);
    const [w, h] = /(\d+)x(\d+)/.exec(adb("shell", "wm", "size")).slice(1).map(Number);
    adb("shell", "input", "tap", String(Math.round(w / 2)), String(Math.round(h * 0.875)));
    await find(/Show transcript|Transcribing on this phone|Transcription finished/, 60000);
    await find("Show transcript", 180000);
  });
}

const log = JSON.parse(readFileSync(new URL("./fake-connect-log.json", import.meta.url), "utf8"));
const posts = log.requests.filter((r) => r.method === "POST");
await flow("requests carry the key, an Idempotency-Key and a correlation id", async () => {
  if (!posts.length) throw new Error("no chat requests reached the fake server");
  for (const p of posts) if (p.auth !== "present" || !/^mico360-meetings-[0-9a-f]{32}$/.test(p.idem) || !/^MM-[0-9a-f]{24}$/.test(p.corr)) throw new Error(JSON.stringify(p));
});
console.log(`redirect probe: ${JSON.stringify(log.redirect)}`);
console.log(failures ? `${failures} flow(s) failed` : "all flows passed");
process.exit(failures ? 1 : 0);
