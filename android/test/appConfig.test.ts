import { describe, expect, it } from "vitest";
import config, { APP_VERSION, VERSION_CODE } from "../app.config";

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
