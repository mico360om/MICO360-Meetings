// Verify the generated native project after prebuild:  node android/tools/check-native.mjs <buildDir>
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.argv[2];
const manifest = readFileSync(join(root, "android/app/src/main/AndroidManifest.xml"), "utf8");
const nsc = readFileSync(join(root, "android/app/src/main/res/xml/network_security_config.xml"), "utf8");
const checks = [
  ["networkSecurityConfig wired", manifest.includes('android:networkSecurityConfig="@xml/network_security_config"')],
  ["cleartext only for listed hosts", nsc.includes('<base-config cleartextTrafficPermitted="false" />') && nsc.includes("ai.mico360.com")],
  ["microphone permission", manifest.includes("android.permission.RECORD_AUDIO")],
  ["microphone foreground service", /foregroundServiceType="microphone"/.test(manifest)],
  ["install packages permission", manifest.includes("android.permission.REQUEST_INSTALL_PACKAGES")],
];
let ok = true;
for (const [name, pass] of checks) { console.log(`${pass ? "PASS" : "FAIL"} ${name}`); ok &&= pass; }
process.exit(ok ? 0 : 1);
