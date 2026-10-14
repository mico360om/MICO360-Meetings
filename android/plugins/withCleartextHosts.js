// Allow plain HTTP only for the named hosts (Android blocks cleartext by default).
const fs = require("fs");
const path = require("path");
const { AndroidConfig, withAndroidManifest, withDangerousMod } = require("expo/config-plugins");

module.exports = function withCleartextHosts(config, { hosts = [] } = {}) {
  config = withDangerousMod(config, ["android", async (c) => {
    const dir = path.join(c.modRequest.platformProjectRoot, "app", "src", "main", "res", "xml");
    fs.mkdirSync(dir, { recursive: true });
    const domains = hosts.map((h) => `    <domain includeSubdomains="false">${h}</domain>`).join("\n");
    fs.writeFileSync(path.join(dir, "network_security_config.xml"),
      `<?xml version="1.0" encoding="utf-8"?>\n<network-security-config>\n` +
      `  <base-config cleartextTrafficPermitted="false" />\n` +
      `  <domain-config cleartextTrafficPermitted="true">\n${domains}\n  </domain-config>\n</network-security-config>\n`);
    return c;
  }]);
  return withAndroidManifest(config, (c) => {
    const app = AndroidConfig.Manifest.getMainApplicationOrThrow(c.modResults);
    app.$["android:networkSecurityConfig"] = "@xml/network_security_config";
    delete app.$["android:usesCleartextTraffic"];
    return c;
  });
};
