import { describe, expect, it } from "vitest";
import { MUTED_MIC_SECONDS, SilenceWatch } from "../src/core/silence";
import { repairWavHeader } from "../src/core/wav";
import { decodeWav16 } from "../src/core/diarization";

describe("muted microphone", () => {
  it("warns once after 8 s of digital silence", () => {
    const w = new SilenceWatch();
    const zeros = new Float32Array(16000);
    const hits = Array.from({ length: MUTED_MIC_SECONDS + 3 }, () => w.feed(zeros, 16000));
    expect(hits.filter(Boolean)).toHaveLength(1);
    expect(hits.indexOf(true)).toBe(MUTED_MIC_SECONDS - 1);
  });
  it("never warns once the mic has picked up any sound", () => {
    const w = new SilenceWatch(1);
    expect(w.feed(Float32Array.of(0, 0.0001, 0), 16000)).toBe(false);
    expect(w.feed(new Float32Array(32000), 16000)).toBe(false);
  });
  it("works on 16-bit samples too", () => {
    expect(new SilenceWatch(1).feed(new Int16Array(16000), 16000)).toBe(true);
  });
});

function wav(samples: number, dataSizeField: number, riffSizeField: number): Uint8Array {
  const buf = new ArrayBuffer(44 + samples * 2);
  const v = new DataView(buf);
  const w = (o: number, s: string) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
  w(0, "RIFF"); v.setUint32(4, riffSizeField, true); w(8, "WAVE"); w(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 16000, true);
  v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data"); v.setUint32(40, dataSizeField, true);
  for (let i = 0; i < samples; i++) v.setInt16(44 + i * 2, 1000, true);
  return new Uint8Array(buf);
}

describe("WAV repair after the app was killed while recording", () => {
  it("fixes zero sizes so every recorded sample is readable", () => {
    const fixed = repairWavHeader(wav(16000, 0, 0))!;
    expect(decodeWav16(fixed).pcm.length).toBe(16000);
    const v = new DataView(fixed.buffer);
    expect(v.getUint32(4, true)).toBe(36 + 32000);
    expect(v.getUint32(40, true)).toBe(32000);
  });
  it("a size field that claims more than was written is corrected too", () => {
    const fixed = repairWavHeader(wav(100, 0xffffffff, 0xffffffff))!;
    expect(decodeWav16(fixed).pcm.length).toBe(100);
  });
  it("leaves a healthy file alone and refuses non-WAV bytes", () => {
    const ok = wav(10, 20, 56);
    expect(repairWavHeader(ok)).toBe(ok);
    expect(repairWavHeader(new Uint8Array([1, 2, 3]))).toBeNull();
  });
});
