import type { ExpoConfig } from "expo/config";
import { randomBytes } from "node:crypto";

export const APP_VERSION = "1.0.0";
export const VERSION_CODE = 1;

/** XOR the Cloud key with a per-build salt; src/core/keyBox.ts reveals it in the app.
 *  Self-contained because Expo's config loader cannot import other TypeScript files. */
function hideKey(key: string): { s: string; d: string } {
  const salt = randomBytes(32);
  const data = Buffer.from(key, "utf8").map((b, i) => b ^ salt[i % salt.length]);
  return { s: salt.toString("hex"), d: Buffer.from(data).toString("hex") };
}

export default function config(): ExpoConfig {
  const key = (process.env.MICO360_MOBILE_CONNECT_KEY || process.env.MICO360_CONNECT_API_KEY || "").trim();
  return {
    name: "MICO360 Meetings",
    slug: "mico360-meetings",
    scheme: "mico360meetings",
    version: APP_VERSION,
    orientation: "portrait",
    icon: "./assets/icon.png",
    userInterfaceStyle: "automatic",
    android: {
      package: "com.mico360.meetings",
      versionCode: VERSION_CODE,
      adaptiveIcon: {
        backgroundColor: "#8B1E1E",
        foregroundImage: "./assets/android-icon-foreground.png",
        backgroundImage: "./assets/android-icon-background.png",
        monochromeImage: "./assets/android-icon-monochrome.png",
      },
      predictiveBackGestureEnabled: false,
      permissions: ["RECORD_AUDIO", "POST_NOTIFICATIONS", "REQUEST_INSTALL_PACKAGES"],
    },
    plugins: [
      "expo-router",
      "expo-sqlite",
      "expo-sharing",
      ["@siteed/audio-studio", { enablePhoneStateHandling: true, enableNotifications: true, enableBackgroundAudio: true, enableDeviceDetection: false }],
      ["expo-notifications", { color: "#8B1E1E" }],
      ["expo-build-properties", { android: { minSdkVersion: 26 } }],
      // Plain HTTP only to MICO360 Connect; an end-to-end test build may also reach the emulator's host.
      ["./plugins/withCleartextHosts.js", { hosts: ["ai.mico360.com", ...(process.env.MICO360_E2E_CONNECT_URL ? ["10.0.2.2", "localhost", "127.0.0.1"] : [])] }],
      "./plugins/withNinja.js",
    ],
    experiments: { typedRoutes: false },
    extra: {
      connect: key ? hideKey(key) : null,
      e2eConnectUrl: process.env.MICO360_E2E_CONNECT_URL ?? "",
    },
  };
}
