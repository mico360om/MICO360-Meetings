// Release signing from environment variables read by Gradle at build time, so no
// keystore path or password is ever written into the project:
//   MICO360_KEYSTORE_PATH, MICO360_KEYSTORE_PASSWORD, MICO360_KEY_ALIAS, MICO360_KEY_PASSWORD
// Without MICO360_KEYSTORE_PATH a local release build stays debug-signed.
const { withAppBuildGradle } = require("expo/config-plugins");

const MARKER = "// mico360: release signing";

function addReleaseSigning(gradle) {
  if (gradle.includes(MARKER)) return gradle;
  const release = `
        ${MARKER}
        release {
            if (System.getenv("MICO360_KEYSTORE_PATH")) {
                storeFile file(System.getenv("MICO360_KEYSTORE_PATH"))
                storePassword System.getenv("MICO360_KEYSTORE_PASSWORD")
                keyAlias System.getenv("MICO360_KEY_ALIAS")
                keyPassword System.getenv("MICO360_KEY_PASSWORD")
            }
        }`;
  let out = gradle.replace(/signingConfigs\s*\{/, (m) => m + release);
  // The release build type: the first `signingConfig signingConfigs.debug` after `release {` in buildTypes.
  const bt = out.indexOf("buildTypes");
  const rel = out.indexOf("release {", bt);
  const at = out.indexOf("signingConfig signingConfigs.debug", rel);
  if (bt < 0 || rel < 0 || at < 0) throw new Error("withReleaseSigning: unexpected app/build.gradle layout");
  out = out.slice(0, at) + 'signingConfig System.getenv("MICO360_KEYSTORE_PATH") ? signingConfigs.release : signingConfigs.debug'
    + out.slice(at + "signingConfig signingConfigs.debug".length);
  return out;
}

module.exports = function withReleaseSigning(config) {
  return withAppBuildGradle(config, (c) => {
    c.modResults.contents = addReleaseSigning(c.modResults.contents);
    return c;
  });
};
module.exports.addReleaseSigning = addReleaseSigning;
