/** MICO360 Cloud with the key built into the APK (never shown, logged or stored). */
import Constants from "expo-constants";
import { CloudClient, defaultEndpoint } from "../core/cloudClient";
import { type KeyBox, revealKey } from "../core/keyBox";
import type { AppSettings } from "../data/settings";

const extra = () => (Constants.expoConfig?.extra ?? {}) as { connect?: KeyBox | null; e2eConnectUrl?: string };

export function cloudKey(): string {
  return revealKey(extra().connect ?? null);
}

/** False in a Local-only build (no key was available when the APK was built). */
export function cloudEnabled(): boolean {
  return cloudKey().length > 0;
}

export function makeCloudClient(settings: AppSettings): CloudClient {
  return new CloudClient({ key: cloudKey(), model: settings.cloudModel || undefined, baseUrl: extra().e2eConnectUrl || undefined,
    userAgent: `MICO360 Meetings Android/${Constants.expoConfig?.version ?? ""}` });
}

/** Switch to HTTPS as soon as the server offers it (spec §5.1, guide §9). */
export async function probeCloud(): Promise<void> {
  if (!extra().e2eConnectUrl) await defaultEndpoint.probeHttps();
}
