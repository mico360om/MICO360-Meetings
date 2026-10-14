import { describe, expect, it } from "vitest";
import { ConsentRequired, generateMinutesFor, meetingPreamble, type MinutesClient } from "../src/services/generation";
import { DEFAULT_SETTINGS } from "../src/data/settings";
import type { Meeting } from "../src/data/meetings";

const m = { id: "m", title: "Budget", meetingDate: "5 Oct 2026", attendees: "Ali, Sara, ", transcript: "We agreed." } as Meeting;

function client(encrypted: boolean, seen: Array<[string, string]>): MinutesClient {
  return { model: "llama3.1:latest", warnings: ["processed on qwen instead"], isEncrypted: () => encrypted,
    async generateMinutes(t, tpl) { seen.push([t, tpl]); return "# Minutes"; } };
}

describe("generation", () => {
  it("known meeting details guide the model (desktop format_preamble)", () => {
    expect(meetingPreamble(m)).toBe("Known meeting details (use these exactly in the minutes header):\n" +
      "Meeting Title: Budget\nDate and Time: 5 Oct 2026\nAttendees: Ali, Sara\n\n");
    expect(meetingPreamble({ title: "", meetingDate: "", attendees: "" })).toBe("");
  });
  it("plain HTTP needs the user's consent first", async () => {
    const seen: Array<[string, string]> = [];
    await expect(generateMinutesFor(m, "T", "Formal Minutes", client(false, seen), DEFAULT_SETTINGS)).rejects.toBeInstanceOf(ConsentRequired);
    expect(seen).toEqual([]);
  });
  it("sends preamble + transcript and returns the minutes, warnings and model", async () => {
    const seen: Array<[string, string]> = [];
    const r = await generateMinutesFor(m, "T", "Formal Minutes", client(false, seen), { ...DEFAULT_SETTINGS, plainHttpConsent: true });
    expect(seen[0][0]).toMatch(/^Known meeting details[\s\S]*\n\nWe agreed\.$/);
    expect(seen[0][1]).toBe("T");
    expect(r).toEqual({ minutes: "# Minutes", warnings: ["processed on qwen instead"], model: "llama3.1:latest" });
  });
  it("an encrypted connection needs no consent", async () => {
    await expect(generateMinutesFor(m, "T", "x", client(true, []), DEFAULT_SETTINGS)).resolves.toMatchObject({ minutes: "# Minutes" });
  });
  it("an empty transcript is refused before any request", async () => {
    await expect(generateMinutesFor({ ...m, transcript: " " }, "T", "x", client(true, []), DEFAULT_SETTINGS)).rejects.toThrow(/transcript/i);
  });
});
