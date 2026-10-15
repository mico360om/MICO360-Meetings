import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";
import { describe, expect, it } from "vitest";
import { checkModel, modelById, resumePlan, sha256Chunks, WHISPER_MODELS } from "../src/core/models";
import { ModelMissing, transcribeMeetingAudio } from "../src/services/transcription";
import { DEFAULT_SETTINGS } from "../src/data/settings";

describe("model catalogue", () => {
  it("pins three quantised models from the whisper.cpp repository", () => {
    expect(WHISPER_MODELS.map((m) => m.id)).toEqual(["tiny", "base", "small"]);
    for (const m of WHISPER_MODELS) {
      expect(m.url).toBe(`https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${m.file}`);
      expect(m.sha256).toMatch(/^[0-9a-f]{64}$/);
    }
    expect(modelById("base").file).toBe("ggml-base-q5_1.bin");
  });
  it("hashes a file in chunks exactly like a one-shot hash", async () => {
    const data = Uint8Array.from({ length: 100_003 }, (_x, i) => (i * 31) % 251);
    const digest = await sha256Chunks(async (o, n) => data.subarray(o, o + n), data.length, 4096);
    expect(digest).toBe(bytesToHex(sha256(data)));
  });
  it("a short or altered download is never accepted", () => {
    const m = modelById("tiny");
    expect(checkModel(m, m.sizeBytes - 1, m.sha256)).toBe("size");
    expect(checkModel(m, m.sizeBytes, "0".repeat(64))).toBe("checksum");
    expect(checkModel(m, m.sizeBytes, m.sha256.toUpperCase())).toBe("ok");
  });
});

describe("transcription service", () => {
  it("asks for the model when it is not downloaded", async () => {
    await expect(transcribeMeetingAudio("file:///a.wav", DEFAULT_SETTINGS, {
      modelPath: async () => null, recognizer: async () => { throw new Error("unused"); },
      source: () => ({ stream: async () => ({ durationMs: 0 }) }), durationMs: async () => 0,
    })).rejects.toBeInstanceOf(ModelMissing);
  });
  it("uses the chosen model, language and speaker setting", async () => {
    const seen: string[] = [];
    const t = await transcribeMeetingAudio("file:///a.wav", { ...DEFAULT_SETTINGS, whisperModel: "small", language: "ar", diarize: false }, {
      modelPath: async (id) => { seen.push(id); return "/models/" + id; },
      recognizer: async (p) => { seen.push(p); return { transcribe: async (_s, lang) => { seen.push(lang); return { language: lang, segments: [{ text: "مرحبا", t0: 0, t1: 100 }] }; } }; },
      source: () => ({ stream: async (on) => { await on(new Float32Array(16000)); return { durationMs: 1000 }; } }),
      durationMs: async () => 1000,
    });
    expect(seen).toEqual(["small", "/models/small", "ar"]);
    expect(t.text).toBe("مرحبا");
  });
});

describe("resuming a model download", () => {
  it("appends the rest, replaces when the server ignored the range, otherwise starts over", () => {
    expect(resumePlan(400, 600, 1000)).toBe("append");
    expect(resumePlan(400, 1000, 1000)).toBe("replace");
    expect(resumePlan(400, 300, 1000)).toBe("restart");
    expect(resumePlan(0, 0, 1000)).toBe("restart");
  });
});
