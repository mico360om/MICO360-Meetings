# Emulator end-to-end flows

`run.mjs` drives the installed app with `adb` + `uiautomator` (no Maestro needed) through the
accessibility labels the screens set. `fakeConnect.mjs` stands in for MICO360 Connect.

```bash
node android/e2e/fakeConnect.mjs 8787                     # keep running in another terminal
MICO360_E2E_CONNECT_URL=http://10.0.2.2:8787/v1 MICO360_MOBILE_CONNECT_KEY=e2e-local-test \
  powershell -File android/tools/build-mirror.ps1 -Variant release -Abis x86_64
node android/e2e/run.mjs emulator-5554 "$USERPROFILE/mico360-build/app/android/app/build/outputs/apk/release/app-release.apk" [--device]
```

Flows: app opens with four tabs · paste a transcript → generate (the fake server first answers with a
cross-port `307`; the app must refuse it) → retry → minutes · export a PDF → share sheet · the action
item on the Action Items tab · add a company · every chat request carries the key, an
`Idempotency-Key` and an `X-Mico-Correlation-Id`. With `--device`: download the tiny Whisper model
(verified by SHA256), record, stop and transcribe on the device.

The run prints `redirect probe: {...}` — whether the phone's HTTP stack followed the redirect and
whether the `Authorization` header went along (recorded in the spec, §5.1).

Notes: the recording screen never goes idle (timer, level meter), so uiautomator can't read it — the
flow taps *Stop* by its place on screen. An E2E build points at the fake server and must never be
published.
