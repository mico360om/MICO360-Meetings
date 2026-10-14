import { describe, expect, it } from "vitest";
import config, { APP_VERSION, VERSION_CODE } from "../app.config";
import { revealKey } from "../src/core/keyBox";

describe("app config", () => {
  it("identifies the Android app", () => {
    const c = config();
    expect(c.name).toBe("MICO360 Meetings");
    expect(c.android?.package).toBe("com.mico360.meetings");
    expect(c.version).toBe(APP_VERSION);
    expect(APP_VERSION).toBe("1.0.0");
    expect(c.android?.versionCode).toBe(VERSION_CODE);
  });
});

describe("build-time configuration", () => {
  it("hides the Connect key from the environment and enables the native plugins", () => {
    process.env.MICO360_MOBILE_CONNECT_KEY = "mico_test_key";
    const c = config();
    delete process.env.MICO360_MOBILE_CONNECT_KEY;
    expect(revealKey((c.extra as { connect: { s: string; d: string } }).connect)).toBe("mico_test_key");
    expect(JSON.stringify(c)).not.toContain("mico_test_key");
    const names = (c.plugins ?? []).map((p) => (Array.isArray(p) ? p[0] : p));
    expect(names).toEqual(expect.arrayContaining(["expo-router", "expo-sqlite", "expo-sharing", "@siteed/audio-studio",
      "expo-notifications", "expo-build-properties", "./plugins/withCleartextHosts.js"]));
  });
  it("no key in the environment: a Local-only build", () => {
    delete process.env.MICO360_MOBILE_CONNECT_KEY;
    delete process.env.MICO360_CONNECT_API_KEY;
    expect((config().extra as { connect: unknown }).connect).toBeNull();
  });
});
