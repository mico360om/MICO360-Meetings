const fs = require("fs");
const path = require("path");
const { _electron: electron } = require("playwright-core");

const rootDir = path.resolve(__dirname, "..");
const artifactDir = path.join(rootDir, "qa-artifacts", "ui-qa");
const electronExe = process.platform === "win32"
  ? path.join(rootDir, "node_modules", "electron", "dist", "electron.exe")
  : path.join(rootDir, "node_modules", ".bin", "electron");

const viewports = [
  { name: "small-laptop", width: 1024, height: 720 },
  { name: "standard-desktop", width: 1366, height: 900 },
  { name: "large-monitor", width: 1920, height: 1080 },
  { name: "tablet-width", width: 820, height: 900 },
  { name: "mobile-width", width: 390, height: 760 }
];

const settingsTabs = [
  { name: "settings-ai", tab: "ai" },
  { name: "settings-transcription", tab: "transcription" },
  { name: "settings-company", tab: "company" },
  { name: "settings-prompts", tab: "prompts" }
];

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

async function collectLayoutIssues(page) {
  return page.evaluate(() => {
    const issues = [];
    const width = document.documentElement.clientWidth;
    const height = document.documentElement.clientHeight;
    const pageOverflow = document.documentElement.scrollWidth - width;
    if (pageOverflow > 2) {
      issues.push({
        type: "page-horizontal-overflow",
        detail: `${Math.round(pageOverflow)}px overflow`
      });
    }

    const selectors = [
      "button",
      "input",
      "select",
      "textarea",
      ".panel",
      ".settings-panel",
      ".profile-preview",
      ".toast",
      ".history-list"
    ];

    document.querySelectorAll(selectors.join(",")).forEach((element) => {
      const rect = element.getBoundingClientRect();
      const label = element.id || element.textContent?.trim()?.slice(0, 60) || element.tagName.toLowerCase();
      if (rect.width <= 0 || rect.height <= 0) return;
      if (rect.left < -2 || rect.right > width + 2) {
        const style = getComputedStyle(element);
        const canScroll = /(auto|scroll)/.test(style.overflow + style.overflowX + style.overflowY);
        if (!canScroll && !element.closest(".settings-content")) {
          issues.push({
            type: "element-horizontal-overflow",
            label,
            rect: {
              left: Math.round(rect.left),
              top: Math.round(rect.top),
              right: Math.round(rect.right),
              bottom: Math.round(rect.bottom)
            }
          });
        }
      }
    });

    document.querySelectorAll("button, input, select").forEach((element) => {
      const text = element.textContent?.trim() || element.value || element.getAttribute("placeholder") || element.id || element.tagName;
      if (element.scrollWidth > element.clientWidth + 4 && element.clientWidth > 0) {
        issues.push({
          type: "control-text-overflow",
          label: text.slice(0, 60),
          overflow: Math.round(element.scrollWidth - element.clientWidth)
        });
      }
    });

    return issues;
  });
}

async function screenshot(page, viewportName, screenName) {
  const filePath = path.join(artifactDir, `${viewportName}-${screenName}.png`);
  await page.screenshot({ path: filePath, fullPage: false });
  return path.relative(rootDir, filePath);
}

async function main() {
  ensureDir(artifactDir);
  const app = await electron.launch({
    executablePath: electronExe,
    args: [rootDir],
    env: {
      ...process.env,
      MICO360_DISABLE_AUTO_UPDATE: "1",
      MICO360_USER_DATA_DIR: path.join(rootDir, "qa-artifacts", "user-data")
    }
  });

  const page = await app.firstWindow();
  page.on("dialog", (dialog) => dialog.accept());
  await page.waitForSelector(".app-shell", { timeout: 30000 });
  await page.waitForTimeout(1000);

  const results = [];

  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.waitForTimeout(250);
    results.push({
      viewport: viewport.name,
      screen: "dashboard",
      screenshot: await screenshot(page, viewport.name, "dashboard"),
      issues: await collectLayoutIssues(page)
    });

    await page.click("#settingsToggle");
    await page.waitForSelector("#settingsPanel:not(.hidden)");
    for (const item of settingsTabs) {
      await page.click(`.settings-tab[data-tab="${item.tab}"]`);
      await page.waitForTimeout(200);
      results.push({
        viewport: viewport.name,
        screen: item.name,
        screenshot: await screenshot(page, viewport.name, item.name),
        issues: await collectLayoutIssues(page)
      });
    }
    await page.click("#settingsClose");
    await page.waitForSelector("#settingsPanel", { state: "hidden" });
  }

  await page.setViewportSize({ width: 1366, height: 900 });
  await page.waitForTimeout(250);
  await page.click("#generateBtn");
  await page.waitForSelector(".toast.error", { timeout: 5000 });
  results.push({
    viewport: "standard-desktop",
    screen: "empty-generate-validation",
    screenshot: await screenshot(page, "standard-desktop", "empty-generate-validation"),
    issues: await collectLayoutIssues(page)
  });

  await app.close();

  const reportPath = path.join(artifactDir, "ui-qa-results.json");
  fs.writeFileSync(reportPath, JSON.stringify(results, null, 2), "utf8");
  const issueCount = results.reduce((total, result) => total + result.issues.length, 0);
  console.log(JSON.stringify({
    screens: results.length,
    issueCount,
    report: path.relative(rootDir, reportPath)
  }, null, 2));
  if (issueCount) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
