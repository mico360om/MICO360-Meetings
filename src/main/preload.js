const { contextBridge, ipcRenderer } = require("electron");
const { OUTPUT_STYLES, DEFAULT_PROMPT, PROMPT_PRESETS } = require("../shared/constants");

contextBridge.exposeInMainWorld("mico360", {
  constants: { outputStyles: OUTPUT_STYLES, defaultPrompt: DEFAULT_PROMPT, promptPresets: PROMPT_PRESETS },
  chooseFile: () => ipcRenderer.invoke("dialog:choose-file"),
  chooseLogo: () => ipcRenderer.invoke("dialog:choose-logo"),
  importProfiles: () => ipcRenderer.invoke("profiles:import"),
  exportProfiles: (payload) => ipcRenderer.invoke("profiles:export", payload),
  getModels: () => ipcRenderer.invoke("ollama:get-models"),
  transcribeFile: (payload) => ipcRenderer.invoke("meeting:transcribe-file", payload),
  ingestFiles: (payload) => ipcRenderer.invoke("meeting:ingest-files", payload),
  generateMinutes: (payload) => ipcRenderer.invoke("meeting:generate-minutes", payload),
  saveRecording: (payload) => ipcRenderer.invoke("meeting:save-recording", payload),
  exportMinutes: (payload) => ipcRenderer.invoke("meeting:export-minutes", payload),
  saveProject: (payload) => ipcRenderer.invoke("history:save-project", payload),
  listProjects: () => ipcRenderer.invoke("history:list-projects"),
  deleteProject: (id) => ipcRenderer.invoke("history:delete-project", id),
  getSettings: () => ipcRenderer.invoke("settings:get"),
  saveSettings: (payload) => ipcRenderer.invoke("settings:save", payload),
  checkForUpdates: () => ipcRenderer.invoke("updates:check"),
  installUpdate: () => ipcRenderer.invoke("updates:install"),
  openPath: (filePath) => ipcRenderer.invoke("shell:open-path", filePath),
  onProgress: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("progress:event", listener);
    return () => ipcRenderer.removeListener("progress:event", listener);
  },
  onUpdate: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("update:event", listener);
    return () => ipcRenderer.removeListener("update:event", listener);
  }
});
