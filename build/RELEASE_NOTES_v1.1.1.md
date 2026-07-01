# MICO360 Meetings v1.1.1

Patch release — recording fixes found during full end-to-end testing.

## Fixes
- 🐛 **System / "Mic + System" recording no longer crashes.** An uninitialised
  counter made those two recording modes fail instantly — fixed.
- 🔊 **System-audio capture now works out of the box.** It uses true **WASAPI
  loopback** to record your speaker output, so you no longer need to enable
  Windows "Stereo Mix". Verified capturing live playback. (Falls back to a
  Stereo-Mix device if loopback is unavailable.)

Microphone-only recording, transcription, minutes generation, exports and all
other features are unchanged. **74 integration + 29 per-module tests pass.**

Everything else in v1.1.0 still applies — see that release for the full feature
list. Download **MICO360Meetings-Setup.exe** below and run it.
