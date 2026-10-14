import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";

const require = createRequire(import.meta.url);
const { addReleaseSigning } = require("../plugins/withReleaseSigning.js") as { addReleaseSigning: (gradle: string) => string };

const TEMPLATE = `android {
    signingConfigs {
        debug {
            storeFile file('debug.keystore')
            storePassword 'android'
            keyAlias 'androiddebugkey'
            keyPassword 'android'
        }
    }
    buildTypes {
        debug {
            signingConfig signingConfigs.debug
        }
        release {
            // Caution! In production, you need to generate your own keystore file.
            signingConfig signingConfigs.debug
            minifyEnabled enableMinifyInReleaseBuilds
        }
    }
}`;

describe("release signing", () => {
  it("signs release builds with the keystore named by environment variables, read at build time", () => {
    const out = addReleaseSigning(TEMPLATE);
    expect(out).toContain('System.getenv("MICO360_KEYSTORE_PATH")');
    expect(out).toContain('storePassword System.getenv("MICO360_KEYSTORE_PASSWORD")');
    expect(out).toContain('keyAlias System.getenv("MICO360_KEY_ALIAS")');
    expect(out).toContain('keyPassword System.getenv("MICO360_KEY_PASSWORD")');
    expect(out).toMatch(/release \{[\s\S]*signingConfig System\.getenv\("MICO360_KEYSTORE_PATH"\) \? signingConfigs\.release : signingConfigs\.debug/);
    expect(out.match(/signingConfig signingConfigs\.debug/g)).toHaveLength(1);   // the debug build type keeps debug signing
  });
  it("is applied once", () => {
    const once = addReleaseSigning(TEMPLATE);
    expect(addReleaseSigning(once)).toBe(once);
  });
});
