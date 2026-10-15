// Windows builds: the Android SDK's ninja 1.10 cannot open paths over 260
// characters (React Native codegen objects exceed that). When MICO360_NINJA
// names a long-path-aware ninja (1.12+), the app's CMake build uses it.
const { withAppBuildGradle } = require("expo/config-plugins");

module.exports = function withNinja(config) {
  return withAppBuildGradle(config, (c) => {
    const ninja = (process.env.MICO360_NINJA || "").replace(/\\/g, "/");
    const marker = "// mico360: ninja";
    if (!ninja || c.modResults.contents.includes(marker)) return c;
    c.modResults.contents = c.modResults.contents.replace(/defaultConfig\s*\{/, (m) =>
      `${m}\n        ${marker}\n        externalNativeBuild { cmake { arguments "-DCMAKE_MAKE_PROGRAM=${ninja}" } }`);
    return c;
  });
};
