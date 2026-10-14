/** M37 on the phone: a working microphone always picks up some noise; a stream
 *  of exact zeros means it is muted (or the audio engine is wedged). */
export const MUTED_MIC_SECONDS = 8;
export const MUTED_MIC_MESSAGE =
  "The microphone is sending complete silence — it may be muted (on the phone, the headset or by another app). Check it, or stop and try again.";

export class SilenceWatch {
  private silent = 0;
  private done = false;
  constructor(private readonly seconds = MUTED_MIC_SECONDS) {}

  /** Feed captured samples; true exactly once, when only silence has arrived for `seconds`. */
  feed(samples: ArrayLike<number>, sampleRate: number): boolean {
    if (this.done) return false;
    for (let i = 0; i < samples.length; i++) {
      if (samples[i] !== 0) { this.done = true; return false; }
    }
    this.silent += samples.length;
    if (this.silent / sampleRate >= this.seconds) { this.done = true; return true; }
    return false;
  }

  reset(): void { this.silent = 0; this.done = false; }
}
