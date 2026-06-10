const path = require("path");
const { app, BrowserWindow, dialog, ipcMain, shell, nativeImage, Notification } = require("electron");
const { autoUpdater } = require("electron-updater");
const Store = require("electron-store");
const {
  appendLog,
  cleanTranscript,
  convertToWav,
  createProjectRecord,
  detectMediaType,
  extractTextFile,
  exportCompanyProfiles,
  exportMinutes,
  fetchOllamaModels,
  generateMinutes,
  importCompanyProfiles,
  saveBufferToFile,
  transcribeWithLocalTool
} = require("./services");

if (process.env.MICO360_USER_DATA_DIR) {
  app.setPath("userData", process.env.MICO360_USER_DATA_DIR);
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) app.quit();

const store = new Store({
  defaults: {
    settings: {
      theme: "light",
      model: "qwen2.5:0.5b",
      outputStyle: "Formal Minutes",
      transcriptionEngine: "faster-whisper",
      whisperModel: "tiny",
      ffmpegPath: "",
      companyName: "MICO360",
      preparedBy: "MICO360 Meetings",
      classification: "Confidential",
      pdfFooter: "Generated locally by MICO360 Meetings",
      includeLogo: true,
      activeCompanyProfileId: "default",
      companyProfiles: [
        {
          id: "default",
          name: "MICO360",
          companyName: "MICO360",
          preparedBy: "MICO360 Meetings",
          classification: "Confidential",
          pdfFooter: "Generated locally by MICO360 Meetings",
          includeLogo: true,
          logoPath: ""
        }
      ],
      promptTemplate: require("../shared/constants").DEFAULT_PROMPT
    },
    projects: []
  }
});

let mainWindow;
let updateReady = false;
let updateCheckTimer = null;
let lastUpdateNotification = "";
let lastUpdateInfo = null;

function formatReleaseNotes(notes) {
  if (Array.isArray(notes)) {
    return notes
      .map((item) => [item.version ? `Version ${item.version}` : "", item.note || item.notes || ""].filter(Boolean).join(": "))
      .filter(Boolean)
      .join("\n");
  }
  if (typeof notes === "string") return notes;
  return "";
}

function getUpdatePayload(status, message, extra = {}) {
  const info = extra.info || lastUpdateInfo || {};
  return {
    appName: "MICO360 Meetings",
    currentVersion: app.getVersion(),
    newVersion: extra.version || info.version || "",
    status,
    message,
    updateSize: extra.total || extra.size || info.files?.[0]?.size || 0,
    releaseDate: extra.releaseDate || info.releaseDate || "",
    releaseName: extra.releaseName || info.releaseName || info.name || "",
    updateDescription: extra.releaseNotes || formatReleaseNotes(info.releaseNotes) || extra.description || "",
    progress: extra.percent ?? extra.progress ?? null,
    transferred: extra.transferred || 0,
    total: extra.total || 0,
    restartRequired: Boolean(extra.restartRequired),
    completedAt: extra.completedAt || "",
    errorMessage: extra.errorMessage || "",
    ...extra
  };
}

function sendProgress(stage, message, progress) {
  mainWindow?.webContents.send("progress:event", { stage, message, progress });
}

function sendUpdateStatus(status, message, extra = {}) {
  mainWindow?.webContents.send("update:event", getUpdatePayload(status, message, extra));
}

function showUpdateNotification(title, body, key = `${title}:${body}`) {
  if (!Notification.isSupported() || lastUpdateNotification === key) return;
  lastUpdateNotification = key;
  const notification = new Notification({
    title,
    body,
    icon: path.join(__dirname, "../../assets/app-icon.png")
  });
  notification.on("click", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
      sendUpdateStatus("focus", "Open Settings -> AI -> Application Updates to manage the update.");
    }
  });
  notification.show();
}

async function checkForUpdatesQuietly() {
  if (!app.isPackaged) {
    sendUpdateStatus("dev", "Auto updates are available only in the installed app.");
    return null;
  }
  try {
    return await autoUpdater.checkForUpdates();
  } catch (error) {
    appendLog(app.getPath("userData"), "Automatic update check failed", error.stack || error.message);
    sendUpdateStatus("error", error.message || "Automatic update check failed.");
    return null;
  }
}

function configureAutoUpdater() {
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.on("checking-for-update", () => sendUpdateStatus("checking", "Checking GitHub for updates..."));
  autoUpdater.on("update-available", (info) => {
    updateReady = false;
    lastUpdateInfo = info;
    const version = info.version || "latest";
    sendUpdateStatus("available", `Update ${version} found. Downloading now...`, { info, version: info.version, restartRequired: true });
    showUpdateNotification("MICO360 Meetings update found", `Version ${version} is downloading now.`, `available:${version}`);
  });
  autoUpdater.on("update-not-available", (info) => {
    updateReady = false;
    lastUpdateInfo = info;
    sendUpdateStatus("none", `MICO360 Meetings is up to date${info.version ? ` (${info.version})` : ""}.`, { info, version: info.version });
  });
  autoUpdater.on("download-progress", (progress) => {
    const percent = Math.round(progress.percent || 0);
    sendUpdateStatus("downloading", `Downloading update ${percent}%`, {
      percent: progress.percent || 0,
      transferred: progress.transferred,
      total: progress.total,
      restartRequired: true
    });
    if (percent >= 50 && percent < 60) {
      showUpdateNotification("MICO360 Meetings update", "Update download is more than halfway complete.", "download-half");
    }
  });
  autoUpdater.on("update-downloaded", (info) => {
    updateReady = true;
    lastUpdateInfo = info;
    const version = info.version || "latest";
    sendUpdateStatus("ready", `Update ${version} is ready to install.`, { info, version: info.version, progress: 100, restartRequired: true });
    showUpdateNotification("MICO360 Meetings update ready", "Open the app and click Install Update, or restart to install.", `ready:${version}`);
  });
  autoUpdater.on("error", (error) => {
    appendLog(app.getPath("userData"), "Auto update failed", error.stack || error.message);
    sendUpdateStatus("error", error.message || "Update check failed.", { errorMessage: error.message || "Update check failed." });
    showUpdateNotification("MICO360 Meetings update failed", error.message || "Update check failed.", `error:${error.message}`);
  });
}

function createWindow() {
  const icon = nativeImage.createFromPath(path.join(__dirname, "../../assets/app-icon.png"));
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 360,
    minHeight: 560,
    title: "MICO360 Meetings",
    icon,
    backgroundColor: "#f7f8fa",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false
    }
  });

  mainWindow.loadFile(path.join(__dirname, "../renderer/index.html"));
  mainWindow.once("ready-to-show", () => {
    const completedUpdate = store.get("pendingUpdateCompletion");
    if (completedUpdate?.version) {
      const completedAt = new Date().toISOString();
      store.delete("pendingUpdateCompletion");
      setTimeout(() => {
        sendUpdateStatus("completed", `MICO360 Meetings ${completedUpdate.version} installed successfully.`, {
          version: completedUpdate.version,
          progress: 100,
          completedAt,
          restartRequired: false
        });
        showUpdateNotification("MICO360 Meetings updated", `Version ${completedUpdate.version} installed successfully.`, `completed:${completedUpdate.version}`);
      }, 1500);
    }
    updateCheckTimer = setTimeout(() => checkForUpdatesQuietly(), 5000);
  });
}

app.whenReady().then(createWindow);
app.whenReady().then(configureAutoUpdater);

app.on("second-instance", () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

app.on("window-all-closed", () => {
  if (updateCheckTimer) clearTimeout(updateCheckTimer);
  if (process.platform !== "darwin") app.quit();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

ipcMain.handle("dialog:choose-file", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Select meeting files",
    properties: ["openFile", "multiSelections"],
    filters: [
      { name: "Meeting Files", extensions: ["mp3", "wav", "m4a", "weba", "mp4", "mov", "mkv", "webm", "txt", "md", "csv", "json", "pdf", "docx", "png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff"] },
      { name: "Audio", extensions: ["mp3", "wav", "m4a", "weba"] },
      { name: "Video", extensions: ["mp4", "mov", "mkv", "webm"] },
      { name: "Documents", extensions: ["txt", "md", "csv", "json", "pdf", "docx"] },
      { name: "Images", extensions: ["png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff"] }
    ]
  });
  if (result.canceled) return [];
  return result.filePaths;
});

ipcMain.handle("dialog:choose-logo", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Select company logo",
    properties: ["openFile"],
    filters: [{ name: "Images", extensions: ["png", "jpg", "jpeg"] }]
  });
  if (result.canceled || !result.filePaths[0]) return null;

  const sourcePath = result.filePaths[0];
  const logoDir = path.join(app.getPath("userData"), "company-logos");
  require("fs").mkdirSync(logoDir, { recursive: true });
  const ext = path.extname(sourcePath).toLowerCase();
  const destination = path.join(logoDir, `logo-${Date.now()}${ext}`);
  require("fs").copyFileSync(sourcePath, destination);
  return destination;
});

ipcMain.handle("profiles:import", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Import company profiles",
    properties: ["openFile"],
    filters: [
      { name: "Profile Files", extensions: ["json", "csv", "xlsx", "docx", "pdf"] }
    ]
  });
  if (result.canceled || !result.filePaths[0]) return [];
  return importCompanyProfiles(result.filePaths[0]);
});

ipcMain.handle("profiles:export", async (_event, payload) => {
  const format = payload.format || "json";
  const result = await dialog.showSaveDialog(mainWindow, {
    title: `Export Company Profiles ${format.toUpperCase()}`,
    defaultPath: `MICO360-Company-Profiles.${format}`,
    filters: [{ name: format.toUpperCase(), extensions: [format] }]
  });
  if (result.canceled || !result.filePath) return null;
  return exportCompanyProfiles({
    format,
    profiles: payload.profiles || [],
    destinationPath: result.filePath
  });
});

ipcMain.handle("ollama:get-models", async () => {
  try {
    return await fetchOllamaModels();
  } catch (error) {
    appendLog(app.getPath("userData"), "Ollama model listing failed", error.stack || error.message);
    return [];
  }
});

ipcMain.handle("meeting:transcribe-file", async (_event, payload) => {
  try {
    const filePath = payload.filePath;
    const type = detectMediaType(filePath);
    if (type === "transcript") {
      sendProgress("transcription", "Reading transcript file...", 10);
      sendProgress("transcription", "Transcript file loaded.", 100);
      return { transcript: cleanTranscript(require("fs").readFileSync(filePath, "utf8")), filePath };
    }
    if (type === "unknown") throw new Error("Unsupported file type. Use MP3, WAV, M4A, MP4, MOV, MKV, TXT, or MD.");

    const workDir = path.join(app.getPath("userData"), "transcription-work", String(Date.now()));
    sendProgress("transcription", "Preparing audio for transcription...", 8);
    const transcriptionSettings = { ...store.get("settings"), ...payload.settings };
    let convertProgress = 12;
    const wavPath = await convertToWav(filePath, workDir, transcriptionSettings, (line) => {
      convertProgress = Math.min(32, convertProgress + 2);
      sendProgress("transcription", line, convertProgress);
    });
    sendProgress("transcription", "Running local Whisper transcription...", 35);
    let whisperProgress = 38;
    const transcript = await transcribeWithLocalTool({
      inputPath: wavPath,
      workDir,
      settings: transcriptionSettings,
      onProgress: (line) => {
        const compact = String(line || "").trim();
        if (!compact) return;
        whisperProgress = Math.min(92, whisperProgress + (compact.length > 80 ? 3 : 1));
        sendProgress("transcription", compact, whisperProgress);
      }
    });
    sendProgress("transcription", "Cleaning transcript...", 96);
    sendProgress("transcription", "Transcript ready.", 100);
    return { transcript: cleanTranscript(transcript), filePath };
  } catch (error) {
    appendLog(app.getPath("userData"), "Transcription failed", error.stack || error.message);
    throw error;
  }
});

ipcMain.handle("meeting:ingest-files", async (_event, payload) => {
  const filePaths = Array.isArray(payload.filePaths) ? payload.filePaths : [payload.filePath].filter(Boolean);
  if (!filePaths.length) return { transcript: "", files: [] };

  const sections = [];
  const results = [];
  for (let index = 0; index < filePaths.length; index += 1) {
    const filePath = filePaths[index];
    try {
      const type = detectMediaType(filePath);
      sendProgress("upload", `Processing ${index + 1} of ${filePaths.length}: ${path.basename(filePath)}`, Math.round((index / filePaths.length) * 90));
      if (type === "audio" || type === "video") {
        const workDir = path.join(app.getPath("userData"), "transcription-work", String(Date.now()), String(index));
        const transcriptionSettings = { ...store.get("settings"), ...payload.settings };
        const wavPath = await convertToWav(filePath, workDir, transcriptionSettings, (line) => sendProgress("transcription", line, 20 + Math.round((index / filePaths.length) * 40)));
        const transcript = await transcribeWithLocalTool({
          inputPath: wavPath,
          workDir,
          settings: transcriptionSettings,
          onProgress: (line) => sendProgress("transcription", line, 45 + Math.round((index / filePaths.length) * 40))
        });
        sections.push(`Source File: ${path.basename(filePath)}\nFile Path: ${filePath}\n\n${cleanTranscript(transcript) || "No transcript text was produced for this file."}`);
      } else if (type === "transcript" || type === "document") {
        const extractedText = cleanTranscript(await extractTextFile(filePath));
        sections.push(`Source File: ${path.basename(filePath)}\nFile Path: ${filePath}\n\n${extractedText || "No readable text was found in this file. If it is scanned or image-based, add notes manually."}`);
      } else if (type === "image") {
        sections.push([
          `Source File: ${path.basename(filePath)}`,
          `File Path: ${filePath}`,
          "",
          "Image attachment added. Review this image manually and add any visual details that should be included in the meeting record."
        ].join("\n"));
      } else {
        throw new Error("Unsupported file type.");
      }
      results.push({ filePath, status: "ok", type });
    } catch (error) {
      appendLog(app.getPath("userData"), `File ingest failed: ${filePath}`, error.stack || error.message);
      sections.push(`Source File: ${path.basename(filePath)}\nFile Path: ${filePath}\n\nCould not process this file: ${error.message}`);
      results.push({ filePath, status: "failed", error: error.message });
    }
  }
  sendProgress("upload", "Files processed.", 100);
  return { transcript: cleanTranscript(sections.join("\n\n---\n\n")), files: results };
});

ipcMain.handle("meeting:generate-minutes", async (_event, payload) => {
  try {
    sendProgress("ai", "Sending transcript to local Ollama...", 15);
    let aiProgress = 20;
    const minutes = await generateMinutes({
      transcript: payload.transcript,
      model: payload.model,
      style: payload.style,
      promptTemplate: payload.promptTemplate,
      onProgress: (token) => {
        aiProgress = Math.min(95, aiProgress + 1);
        sendProgress("ai", token, aiProgress);
      }
    });
    sendProgress("ai", "Minutes generated.", 100);
    return { minutes };
  } catch (error) {
    appendLog(app.getPath("userData"), "AI generation failed", error.stack || error.message);
    throw error;
  }
});

ipcMain.handle("meeting:save-recording", async (_event, payload) => {
  return saveBufferToFile(payload.buffer, payload.extension || "webm", app.getPath("userData"));
});

ipcMain.handle("meeting:export-minutes", async (_event, payload) => {
  const result = await dialog.showSaveDialog(mainWindow, {
    title: `Export ${payload.format.toUpperCase()}`,
    defaultPath: `MICO360-Meeting-Minutes.${payload.format}`,
    filters: [{ name: payload.format.toUpperCase(), extensions: [payload.format] }]
  });
  if (result.canceled || !result.filePath) return null;
  return exportMinutes({
    format: payload.format,
    content: payload.content,
    destinationPath: result.filePath,
    profile: payload.profile || store.get("settings")
  });
});

ipcMain.handle("history:save-project", async (_event, payload) => {
  const projects = store.get("projects", []);
  const project = payload.id
    ? { ...projects.find((item) => item.id === payload.id), ...payload, updatedAt: new Date().toISOString() }
    : createProjectRecord(payload);
  const next = [project, ...projects.filter((item) => item.id !== project.id)].slice(0, 200);
  store.set("projects", next);
  return project;
});

ipcMain.handle("history:list-projects", async () => store.get("projects", []));

ipcMain.handle("history:delete-project", async (_event, id) => {
  store.set("projects", store.get("projects", []).filter((project) => project.id !== id));
  return true;
});

ipcMain.handle("settings:get", async () => store.get("settings"));

ipcMain.handle("settings:save", async (_event, payload) => {
  store.set("settings", { ...store.get("settings"), ...payload });
  return store.get("settings");
});

ipcMain.handle("updates:check", async () => {
  if (!app.isPackaged) {
    const message = "Auto updates are available only in the installed app.";
    sendUpdateStatus("dev", message);
    return { ok: false, message };
  }
  try {
    const result = await checkForUpdatesQuietly();
    return { ok: true, updateInfo: result?.updateInfo || null };
  } catch (error) {
    appendLog(app.getPath("userData"), "Manual update check failed", error.stack || error.message);
    throw error;
  }
});

ipcMain.handle("updates:install", async () => {
  if (!updateReady) {
    const message = "No downloaded update is ready to install.";
    sendUpdateStatus("error", message, { errorMessage: message });
    return { ok: false, message };
  }
  try {
    sendUpdateStatus("installing", "Installing update. The app will restart to finish.", {
      progress: 100,
      restartRequired: true
    });
    store.set("pendingUpdateCompletion", {
      version: lastUpdateInfo?.version || app.getVersion(),
      startedAt: new Date().toISOString()
    });
    autoUpdater.quitAndInstall(false, true);
    return { ok: true };
  } catch (error) {
    appendLog(app.getPath("userData"), "Update installation failed", error.stack || error.message);
    sendUpdateStatus("error", error.message || "Update installation failed.", {
      errorMessage: error.message || "Update installation failed."
    });
    throw error;
  }
});

ipcMain.handle("shell:open-path", async (_event, filePath) => shell.openPath(filePath));
