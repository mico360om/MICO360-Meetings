const state = {
  selectedFile: null,
  selectedFiles: [],
  currentProject: null,
  mediaRecorder: null,
  recordedChunks: [],
  recording: null,
  settings: {},
  projects: []
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const elements = {
  themeToggle: $("#themeToggle"),
  settingsToggle: $("#settingsToggle"),
  settingsClose: $("#settingsClose"),
  settingsSave: $("#settingsSave"),
  newProject: $("#newProject"),
  saveProjectTop: $("#saveProjectTop"),
  settingsPanel: $("#settingsPanel"),
  modelSelect: $("#modelSelect"),
  styleSelect: $("#styleSelect"),
  projectTitle: $("#projectTitle"),
  refreshModels: $("#refreshModels"),
  checkUpdates: $("#checkUpdates"),
  installUpdate: $("#installUpdate"),
  updateStatus: $("#updateStatus"),
  companyProfileSelect: $("#companyProfileSelect"),
  companyProfileName: $("#companyProfileName"),
  chooseLogo: $("#chooseLogo"),
  logoPath: $("#logoPath"),
  companyAddress: $("#companyAddress"),
  contactPerson: $("#contactPerson"),
  contactEmail: $("#contactEmail"),
  contactPhone: $("#contactPhone"),
  companyWebsite: $("#companyWebsite"),
  logoPosition: $("#logoPosition"),
  logoWidth: $("#logoWidth"),
  logoCustomX: $("#logoCustomX"),
  logoCustomY: $("#logoCustomY"),
  enablePageNumbers: $("#enablePageNumbers"),
  pageNumberPosition: $("#pageNumberPosition"),
  footerAlignment: $("#footerAlignment"),
  footerSpacing: $("#footerSpacing"),
  profileNotes: $("#profileNotes"),
  importProfiles: $("#importProfiles"),
  profileExportFormat: $("#profileExportFormat"),
  exportProfile: $("#exportProfile"),
  exportAllProfiles: $("#exportAllProfiles"),
  previewLogo: $("#previewLogo"),
  previewCompany: $("#previewCompany"),
  previewContact: $("#previewContact"),
  previewFooter: $("#previewFooter"),
  previewPageNumber: $("#previewPageNumber"),
  newProfile: $("#newProfile"),
  saveProfile: $("#saveProfile"),
  deleteProfile: $("#deleteProfile"),
  chooseFile: $("#chooseFile"),
  reTranscript: $("#reTranscript"),
  dropZone: $("#dropZone"),
  selectedFile: $("#selectedFile"),
  fileQueue: $("#fileQueue"),
  transcriptInput: $("#transcriptInput"),
  minutesOutput: $("#minutesOutput"),
  promptTemplate: $("#promptTemplate"),
  promptPreset: $("#promptPreset"),
  promptName: $("#promptName"),
  promptLibraryList: $("#promptLibraryList"),
  applyPromptPreset: $("#applyPromptPreset"),
  newPrompt: $("#newPrompt"),
  savePrompt: $("#savePrompt"),
  deletePrompt: $("#deletePrompt"),
  resetPrompt: $("#resetPrompt"),
  generateBtn: $("#generateBtn"),
  copyBtn: $("#copyBtn"),
  saveProject: $("#saveProject"),
  recordMic: $("#recordMic"),
  recordScreen: $("#recordScreen"),
  recordingPanel: $("#recordingPanel"),
  recordingStatus: $("#recordingStatus"),
  recordingType: $("#recordingType"),
  recordingTimer: $("#recordingTimer"),
  recordingStart: $("#recordingStart"),
  recordingFileName: $("#recordingFileName"),
  recordingFormat: $("#recordingFormat"),
  recordingQuality: $("#recordingQuality"),
  recordingMicStatus: $("#recordingMicStatus"),
  recordingCameraStatus: $("#recordingCameraStatus"),
  recordingFileSize: $("#recordingFileSize"),
  recordingSaveLocation: $("#recordingSaveLocation"),
  pauseRecording: $("#pauseRecording"),
  resumeRecording: $("#resumeRecording"),
  stopRecording: $("#stopRecording"),
  saveRecording: $("#saveRecording"),
  cancelRecording: $("#cancelRecording"),
  recordingSummary: $("#recordingSummary"),
  recordingSummaryText: $("#recordingSummaryText"),
  statusText: $("#statusText"),
  progressDetail: $("#progressDetail"),
  progressBar: $("#progressBar"),
  progressPercent: $("#progressPercent"),
  toastStack: $("#toastStack"),
  historyList: $("#historyList"),
  historySearch: $("#historySearch"),
  engineSelect: $("#engineSelect"),
  whisperModel: $("#whisperModel"),
  whisperCppModel: $("#whisperCppModel"),
  languageInput: $("#languageInput"),
  ffmpegPath: $("#ffmpegPath"),
  companyName: $("#companyName"),
  preparedBy: $("#preparedBy"),
  classification: $("#classification"),
  includeLogo: $("#includeLogo"),
  pdfFooter: $("#pdfFooter"),
  selectedModelLabel: $("#selectedModelLabel"),
  selectedStyleLabel: $("#selectedStyleLabel")
};

function defaultCompanyProfile() {
  return {
    id: "default",
    name: "MICO360",
    companyName: "MICO360",
    address: "",
    contactPerson: "",
    email: "",
    phone: "",
    website: "",
    registrationNumber: "",
    preparedBy: "MICO360 Meetings",
    classification: "Confidential",
    pdfFooter: "Generated locally by MICO360 Meetings",
    includeLogo: true,
    logoPath: "",
    logoPosition: "left",
    logoWidth: 124,
    logoCustomX: 48,
    logoCustomY: 728,
    enablePageNumbers: true,
    pageNumberPosition: "footer-right",
    footerAlignment: "left",
    footerSpacing: 30,
    notes: ""
  };
}

function setProgress(progress = 0, detail = "") {
  const value = Math.max(0, Math.min(100, Number(progress) || 0));
  elements.progressBar.value = value;
  elements.progressPercent.textContent = `${Math.round(value)}%`;
  if (detail) elements.progressDetail.textContent = detail;
}

function setBusy(isBusy, label = "Working...") {
  elements.generateBtn.disabled = isBusy;
  elements.chooseFile.disabled = isBusy;
  elements.reTranscript.disabled = isBusy;
  elements.recordMic.disabled = isBusy;
  elements.recordScreen.disabled = isBusy;
  setProgress(isBusy ? 8 : 0, isBusy ? label : "Meeting data stays local. Ollama is called only on 127.0.0.1.");
  elements.statusText.textContent = isBusy ? label : "Ready";
}

function showStatus(message, progress = null) {
  elements.statusText.textContent = message;
  if (progress !== null) setProgress(progress, message);
}

function showToast(message, type = "info") {
  if (!elements.toastStack) return;
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <strong>${type === "error" ? "Error" : type === "warning" ? "Warning" : "Notice"}</strong>
    <span>${escapeHtml(message)}</span>
    <button type="button" aria-label="Dismiss message">Dismiss</button>
  `;
  const dismiss = () => toast.remove();
  toast.querySelector("button").addEventListener("click", dismiss);
  elements.toastStack.appendChild(toast);
  setTimeout(dismiss, type === "error" ? 9000 : 5200);
}

function setDirty(isDirty) {
  state.isDirty = isDirty;
  const title = state.currentProject?.title || elements.projectTitle.value.trim() || "Untitled Meeting";
  elements.statusText.textContent = isDirty ? `Unsaved changes: ${title}` : "Ready";
  elements.saveProject.textContent = isDirty ? "Save Edits *" : "Save Edits";
  elements.saveProjectTop.textContent = isDirty ? "Save Project *" : "Save Project";
}

function showError(error) {
  const message = error?.message || String(error);
  showStatus(message, 0);
  showToast(message, "error");
}

function cleanMinutesText(text) {
  return String(text || "")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/__(.*?)__/g, "$1")
    .replace(/^#{1,6}\s*/gm, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function getSettingsFromUi() {
  const activeProfile = getProfileFromUi();
  return {
    theme: document.documentElement.dataset.theme || "light",
    model: elements.modelSelect.value,
    outputStyle: elements.styleSelect.value,
    transcriptionEngine: elements.engineSelect.value,
    whisperModel: elements.whisperModel.value.trim() || "base",
    whisperCppModelPath: elements.whisperCppModel.value.trim(),
    language: elements.languageInput.value.trim(),
    ffmpegPath: elements.ffmpegPath.value.trim(),
    companyName: activeProfile.companyName,
    preparedBy: activeProfile.preparedBy,
    classification: activeProfile.classification,
    includeLogo: activeProfile.includeLogo,
    pdfFooter: activeProfile.pdfFooter,
    logoPath: activeProfile.logoPath,
    activeCompanyProfileId: activeProfile.id,
    companyProfiles: upsertProfile(state.settings.companyProfiles || [defaultCompanyProfile()], activeProfile),
    promptTemplate: elements.promptTemplate.value,
    activePromptName: elements.promptName.value.trim(),
    customPrompts: state.settings.customPrompts || []
  };
}

async function persistSettings() {
  state.settings = await window.mico360.saveSettings(getSettingsFromUi());
}

function populateStyles() {
  elements.styleSelect.innerHTML = window.mico360.constants.outputStyles
    .map((style) => `<option value="${style}">${style}</option>`)
    .join("");
}

function populatePromptPresets() {
  const builtIns = Object.keys(window.mico360.constants.promptPresets)
    .map((name) => ({ name, type: "preset" }));
  const custom = (state.settings.customPrompts || [])
    .map((prompt) => ({ name: prompt.name, type: "custom" }));
  elements.promptPreset.innerHTML = [...builtIns, ...custom]
    .map((item) => `<option value="${escapeHtml(`${item.type}:${item.name}`)}">${item.type === "custom" ? "Saved - " : ""}${escapeHtml(item.name)}</option>`)
    .join("");
  renderPromptLibrary();
}

function renderPromptLibrary() {
  const prompts = state.settings.customPrompts || [];
  if (!elements.promptLibraryList) return;
  if (!prompts.length) {
    elements.promptLibraryList.innerHTML = `<span class="empty-state">No saved prompts yet.</span>`;
    return;
  }
  elements.promptLibraryList.innerHTML = prompts.map((prompt) => `
    <div class="prompt-library-item" data-prompt-name="${escapeHtml(prompt.name)}">
      <div>
        <strong>${escapeHtml(prompt.name)}</strong>
        <span>${escapeHtml((prompt.template || "").replace(/\s+/g, " ").slice(0, 120))}</span>
      </div>
      <div class="button-group">
        <button class="secondary" data-prompt-action="view" data-prompt-name="${escapeHtml(prompt.name)}">View/Edit</button>
        <button class="secondary danger" data-prompt-action="delete" data-prompt-name="${escapeHtml(prompt.name)}">Delete</button>
      </div>
    </div>
  `).join("");
}

function updateSummaryLabels() {
  elements.selectedModelLabel.textContent = elements.modelSelect.value || "Not loaded";
  elements.selectedStyleLabel.textContent = elements.styleSelect.value || "Formal Minutes";
}

function getProfiles() {
  const profiles = state.settings.companyProfiles?.length ? state.settings.companyProfiles : [defaultCompanyProfile()];
  return profiles.map((profile) => ({ ...defaultCompanyProfile(), ...profile }));
}

function upsertProfile(profiles, profile) {
  return [profile, ...profiles.filter((item) => item.id !== profile.id)];
}

function getActiveProfile() {
  const profiles = getProfiles();
  const activeId = state.settings.activeCompanyProfileId || "default";
  return profiles.find((profile) => profile.id === activeId) || profiles[0] || defaultCompanyProfile();
}

function getProfileFromUi() {
  return {
    id: elements.companyProfileSelect.value || crypto.randomUUID(),
    name: elements.companyProfileName.value.trim() || elements.companyName.value.trim() || "Company Profile",
    companyName: elements.companyName.value.trim() || "MICO360",
    address: elements.companyAddress.value.trim(),
    contactPerson: elements.contactPerson.value.trim(),
    email: elements.contactEmail.value.trim(),
    phone: elements.contactPhone.value.trim(),
    website: elements.companyWebsite.value.trim(),
    preparedBy: elements.preparedBy.value.trim() || "MICO360 Meetings",
    classification: elements.classification.value || "Confidential",
    includeLogo: elements.includeLogo.checked,
    pdfFooter: elements.pdfFooter.value.trim() || "Generated locally by MICO360 Meetings",
    logoPath: elements.logoPath.value.trim(),
    logoPosition: elements.logoPosition.value,
    logoWidth: Number(elements.logoWidth.value || 124),
    logoCustomX: Number(elements.logoCustomX.value || 48),
    logoCustomY: Number(elements.logoCustomY.value || 728),
    enablePageNumbers: elements.enablePageNumbers.checked,
    pageNumberPosition: elements.pageNumberPosition.value,
    footerAlignment: elements.footerAlignment.value,
    footerSpacing: Number(elements.footerSpacing.value || 30),
    notes: elements.profileNotes.value.trim()
  };
}

function renderCompanyProfiles() {
  const profiles = getProfiles();
  elements.companyProfileSelect.innerHTML = profiles
    .map((profile) => `<option value="${profile.id}">${escapeHtml(profile.name || profile.companyName)}</option>`)
    .join("");
  elements.companyProfileSelect.value = state.settings.activeCompanyProfileId || profiles[0]?.id || "default";
  loadProfileIntoForm(getActiveProfile());
}

function loadProfileIntoForm(profile) {
  const merged = { ...defaultCompanyProfile(), ...profile };
  elements.companyProfileSelect.value = merged.id;
  elements.companyProfileName.value = merged.name || merged.companyName || "";
  elements.companyName.value = merged.companyName || "MICO360";
  elements.companyAddress.value = merged.address || "";
  elements.contactPerson.value = merged.contactPerson || "";
  elements.contactEmail.value = merged.email || "";
  elements.contactPhone.value = merged.phone || "";
  elements.companyWebsite.value = merged.website || "";
  elements.preparedBy.value = merged.preparedBy || "MICO360 Meetings";
  elements.classification.value = merged.classification || "Confidential";
  elements.includeLogo.checked = merged.includeLogo !== false;
  elements.pdfFooter.value = merged.pdfFooter || "Generated locally by MICO360 Meetings";
  elements.logoPath.value = merged.logoPath || "";
  elements.logoPosition.value = merged.logoPosition || "left";
  elements.logoWidth.value = merged.logoWidth || 124;
  elements.logoCustomX.value = merged.logoCustomX || 48;
  elements.logoCustomY.value = merged.logoCustomY || 728;
  elements.enablePageNumbers.checked = merged.enablePageNumbers !== false;
  elements.pageNumberPosition.value = merged.pageNumberPosition || "footer-right";
  elements.footerAlignment.value = merged.footerAlignment || "left";
  elements.footerSpacing.value = merged.footerSpacing || 30;
  elements.profileNotes.value = merged.notes || "";
  updateProfilePreview();
}

async function saveActiveProfile(message = "Company profile saved") {
  const profile = getProfileFromUi();
  const profiles = upsertProfile(getProfiles(), profile);
  state.settings = await window.mico360.saveSettings({
    ...getSettingsFromUi(),
    activeCompanyProfileId: profile.id,
    companyProfiles: profiles
  });
  renderCompanyProfiles();
  updateProfilePreview();
  showStatus(message, 100);
}

function updateProfilePreview() {
  const profile = getProfileFromUi();
  elements.previewCompany.textContent = profile.companyName;
  elements.previewContact.textContent = [profile.address, profile.contactPerson, profile.email, profile.phone, profile.website]
    .filter(Boolean)
    .join(" | ") || "Contact details";
  elements.previewFooter.textContent = profile.pdfFooter;
  elements.previewFooter.style.textAlign = profile.footerAlignment;
  elements.previewFooter.style.bottom = `${Math.max(10, Math.min(42, profile.footerSpacing / 2))}px`;
  elements.previewLogo.textContent = profile.logoPath ? "Attached Logo" : "Default Logo";
  elements.previewLogo.style.width = `${Math.max(54, Math.min(160, profile.logoWidth / 1.6))}px`;
  elements.previewLogo.dataset.position = profile.logoPosition;
  elements.previewPageNumber.hidden = !profile.enablePageNumbers;
  elements.previewPageNumber.dataset.position = profile.pageNumberPosition;
}

function currentProfilesWithActive() {
  return upsertProfile(getProfiles(), getProfileFromUi());
}

async function loadModels() {
  const models = await window.mico360.getModels();
  const preferred = state.settings.model || "qwen2.5:0.5b";
  const options = models.length ? models : ["llama3.1", "qwen2.5", "mistral", "gemma2"];
  elements.modelSelect.innerHTML = options.map((model) => `<option value="${model}">${model}</option>`).join("");
  const firstTextModel = options.find((model) => !/vision/i.test(model));
  const usablePreferred = options.includes(preferred) && !/vision/i.test(preferred);
  elements.modelSelect.value = usablePreferred ? preferred : firstTextModel || options[0];
  updateSummaryLabels();
  if (!models.length) {
    showStatus("Ollama models not detected. Start Ollama or install a model, then refresh.", 0);
  }
}

async function loadSettings() {
  state.settings = await window.mico360.getSettings();
  document.documentElement.dataset.theme = state.settings.theme || "light";
  elements.styleSelect.value = state.settings.outputStyle || "Formal Minutes";
  elements.engineSelect.value = state.settings.transcriptionEngine || "faster-whisper";
  elements.whisperModel.value = state.settings.whisperModel || "base";
  elements.whisperCppModel.value = state.settings.whisperCppModelPath || "";
  elements.languageInput.value = state.settings.language || "";
  elements.ffmpegPath.value = state.settings.ffmpegPath || "";
  elements.companyName.value = state.settings.companyName || "MICO360";
  elements.preparedBy.value = state.settings.preparedBy || "MICO360 Meetings";
  elements.classification.value = state.settings.classification || "Confidential";
  elements.includeLogo.checked = state.settings.includeLogo !== false;
  elements.pdfFooter.value = state.settings.pdfFooter || "Generated locally by MICO360 Meetings";
  renderCompanyProfiles();
  elements.promptTemplate.value = state.settings.promptTemplate || window.mico360.constants.defaultPrompt;
  elements.promptName.value = state.settings.activePromptName || "Default Meeting Minutes";
  populatePromptPresets();
  updateSummaryLabels();
}

function renderHistory(filter = "") {
  const needle = filter.toLowerCase();
  const projects = state.projects.filter((project) => {
    return [project.title, project.minutes, project.transcript].join(" ").toLowerCase().includes(needle);
  });

  elements.historyList.innerHTML = projects.map((project) => `
    <button class="history-item" data-id="${project.id}">
      <strong>${escapeHtml(project.title || "Untitled Meeting")}</strong>
      <span>${new Date(project.updatedAt || project.createdAt).toLocaleString()} - ${escapeHtml(project.style || "")}</span>
    </button>
  `).join("");
}

async function loadHistory() {
  state.projects = await window.mico360.listProjects();
  renderHistory(elements.historySearch.value);
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatDuration(ms = 0) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function formatFileSize(bytes = 0) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function getRecordingFileName(kind) {
  const label = kind === "screen" ? "video" : "audio";
  const stamp = new Date().toISOString().slice(0, 19).replace(/[T:]/g, "-");
  return `mico360-${label}-recording-${stamp}.webm`;
}

function getTrackSummary(track) {
  if (!track) return "Inactive";
  const settings = track.getSettings?.() || {};
  if (settings.width || settings.height) {
    const size = [settings.width, settings.height].filter(Boolean).join("x");
    return [size, settings.frameRate ? `${Math.round(settings.frameRate)} fps` : ""].filter(Boolean).join(" @ ");
  }
  return [settings.sampleRate ? `${settings.sampleRate} Hz` : "", settings.channelCount ? `${settings.channelCount} channel${settings.channelCount === 1 ? "" : "s"}` : ""]
    .filter(Boolean)
    .join(", ") || "Active";
}

function normalizeFilePaths(input) {
  if (!input) return [];
  const values = Array.isArray(input) ? input : [input];
  return values.map((item) => {
    if (typeof item === "string") return item;
    return item?.path || "";
  }).filter(Boolean);
}

function renderFileQueue(files = [], results = []) {
  state.selectedFiles = files;
  state.selectedFile = files[0] || null;
  elements.selectedFile.textContent = files.length
    ? `${files.length} file${files.length === 1 ? "" : "s"} selected`
    : "No file selected";
  elements.fileQueue.innerHTML = files.map((filePath) => {
    const result = results.find((item) => item.filePath === filePath);
    const status = result?.status || "queued";
    const statusText = status === "ok" ? "Processed" : status === "failed" ? "Failed" : "Queued";
    return `
      <div class="file-queue-item ${status}">
        <span>${escapeHtml(filePath.split(/[\\/]/).pop() || filePath)}</span>
        <strong>${escapeHtml(statusText)}</strong>
      </div>
    `;
  }).join("");
}

async function handleFiles(input, options = {}) {
  const replaceTranscript = Boolean(options.replaceTranscript);
  const filePaths = normalizeFilePaths(input);
  if (!filePaths.length) return;
  renderFileQueue(filePaths);
  setBusy(true, `${replaceTranscript ? "Re-transcribing" : "Processing"} ${filePaths.length} file${filePaths.length === 1 ? "" : "s"}...`);
  try {
    setProgress(6, "Preparing selected files");
    const result = await window.mico360.ingestFiles({ filePaths, settings: getSettingsFromUi() });
    renderFileQueue(filePaths, result.files || []);
    const existing = elements.transcriptInput.value.trim();
    const nextTranscript = replaceTranscript || !existing
      ? result.transcript
      : `${existing}\n\n---\n\n${result.transcript}`;
    elements.transcriptInput.value = nextTranscript;
    setDirty(true);
    const failed = (result.files || []).filter((item) => item.status === "failed").length;
    const doneMessage = replaceTranscript ? "Transcript refreshed" : "Files processed";
    showStatus(failed ? `${doneMessage} with ${failed} issue(s)` : doneMessage, 100);
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

async function reTranscriptSelectedFiles() {
  if (!state.selectedFiles.length) {
    showError(new Error("Upload or record a file first, then use Re-Transcript."));
    return;
  }
  await handleFiles(state.selectedFiles, { replaceTranscript: true });
}

async function generateMinutes() {
  const transcript = elements.transcriptInput.value.trim();
  if (!transcript) {
    showError(new Error("Add a transcript or upload a meeting file first."));
    return;
  }

  setBusy(true, "Generating minutes with local Ollama...");
  elements.minutesOutput.value = "";
  try {
    await persistSettings();
    const result = await window.mico360.generateMinutes({
      transcript,
      model: elements.modelSelect.value,
      style: elements.styleSelect.value,
      promptTemplate: elements.promptTemplate.value
    });
    elements.minutesOutput.value = cleanMinutesText(result.minutes);
    showStatus("Minutes generated", 100);
    await saveProject(false);
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

async function saveProject(showSaved = true) {
  const project = await window.mico360.saveProject({
    id: state.currentProject?.id,
    title: elements.projectTitle.value.trim() || "Untitled Meeting",
    sourceType: state.selectedFile ? "file" : "transcript",
    model: elements.modelSelect.value,
    style: elements.styleSelect.value,
    transcript: elements.transcriptInput.value,
    minutes: cleanMinutesText(elements.minutesOutput.value)
  });
  state.currentProject = project;
  setDirty(false);
  await loadHistory();
  if (showSaved) showStatus("Project saved locally", 100);
}

async function exportCurrent(format) {
  if (!elements.minutesOutput.value.trim()) {
    showError(new Error("There are no minutes to export yet."));
    return;
  }
  try {
    await persistSettings();
    const filePath = await window.mico360.exportMinutes({
      format,
      content: cleanMinutesText(elements.minutesOutput.value),
      profile: getProfileFromUi()
    });
    if (filePath) showStatus(`Exported ${format.toUpperCase()}: ${filePath}`, 100);
  } catch (error) {
    showError(error);
  }
}

async function startRecording(kind) {
  if (state.mediaRecorder && state.recording?.status !== "stopped") {
    showError(new Error("A recording is already active. Stop or cancel it before starting another recording."));
    return;
  }

  try {
    const stream = kind === "screen"
      ? await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true })
      : await navigator.mediaDevices.getUserMedia({ audio: true });

    const audioTrack = stream.getAudioTracks()[0];
    const videoTrack = stream.getVideoTracks()[0];
    const preferredMimeType = kind === "screen" ? "video/webm;codecs=vp9,opus" : "audio/webm;codecs=opus";
    const fallbackMimeType = kind === "screen" ? "video/webm" : "audio/webm";
    const mimeType = MediaRecorder.isTypeSupported(preferredMimeType)
      ? preferredMimeType
      : fallbackMimeType;
    const startedAt = new Date();

    state.recordedChunks = [];
    state.recording = {
      kind,
      stream,
      startedAt,
      pausedMs: 0,
      pausedAt: null,
      status: "recording",
      fileName: getRecordingFileName(kind),
      filePath: "",
      blob: null,
      bytes: 0,
      mimeType,
      audioSummary: getTrackSummary(audioTrack),
      videoSummary: kind === "screen" ? getTrackSummary(videoTrack) : "Not used",
      wasCancelled: false
    };

    state.mediaRecorder = new MediaRecorder(stream, { mimeType });
    state.mediaRecorder.ondataavailable = (event) => {
      if (!event.data.size) return;
      state.recordedChunks.push(event.data);
      state.recording.bytes += event.data.size;
      updateRecordingPanel();
    };
    state.mediaRecorder.onstop = () => {
      stopRecordingTimer();
      state.recording.stream.getTracks().forEach((track) => track.stop());
      state.recording.status = "stopped";
      state.recording.blob = state.recording.wasCancelled
        ? null
        : new Blob(state.recordedChunks, { type: mimeType });
      state.recording.bytes = state.recording.blob?.size || state.recording.bytes;
      updateRecordingPanel();
      if (state.recording.wasCancelled) {
        resetRecordingState("Recording cancelled.");
      } else {
        showRecordingSummary("Stopped. Review the summary, then save or cancel.");
        showStatus("Recording stopped. Save or cancel the recording.", 80);
      }
    };
    state.mediaRecorder.start(1000);
    startRecordingTimer();
    updateRecordingPanel();
    showStatus(`${kind === "screen" ? "Video" : "Audio"} recording started.`, 20);
  } catch (error) {
    showError(error);
  }
}

function getActiveRecordingDuration() {
  if (!state.recording?.startedAt) return 0;
  const paused = state.recording.pausedMs + (state.recording.pausedAt ? Date.now() - state.recording.pausedAt : 0);
  return Date.now() - state.recording.startedAt.getTime() - paused;
}

function updateRecordingPanel() {
  const recording = state.recording;
  if (!recording) {
    elements.recordingPanel.classList.add("hidden");
    return;
  }

  const isRecording = recording.status === "recording";
  const isPaused = recording.status === "paused";
  const isStopped = recording.status === "stopped";
  elements.recordingPanel.classList.remove("hidden");
  elements.recordingPanel.dataset.status = recording.status;
  elements.recordingStatus.textContent = isRecording ? "Recording" : isPaused ? "Paused" : "Stopped";
  elements.recordingType.textContent = `Recording type: ${recording.kind === "screen" ? "Video / screen with audio" : "Audio"}`;
  elements.recordingTimer.textContent = formatDuration(isStopped ? recording.durationMs : getActiveRecordingDuration());
  elements.recordingStart.textContent = recording.startedAt.toLocaleString();
  elements.recordingFileName.textContent = recording.fileName;
  elements.recordingFormat.textContent = "WebM";
  elements.recordingQuality.textContent = recording.kind === "screen" ? recording.videoSummary : recording.audioSummary;
  elements.recordingMicStatus.textContent = recording.audioSummary === "Inactive" ? "Inactive" : isPaused ? "Paused" : "Active";
  elements.recordingCameraStatus.textContent = recording.kind === "screen" ? recording.videoSummary : "Not used for audio recording";
  elements.recordingFileSize.textContent = formatFileSize(recording.bytes);
  elements.recordingSaveLocation.textContent = recording.filePath || "App recordings folder";

  elements.pauseRecording.disabled = !isRecording;
  elements.resumeRecording.disabled = !isPaused;
  elements.stopRecording.disabled = isStopped;
  elements.saveRecording.disabled = !isStopped || !recording.blob;
  elements.cancelRecording.disabled = false;
  elements.recordMic.disabled = !isStopped;
  elements.recordScreen.disabled = !isStopped;
}

function startRecordingTimer() {
  stopRecordingTimer();
  state.recording.timerId = setInterval(updateRecordingPanel, 500);
}

function stopRecordingTimer() {
  if (state.recording?.timerId) clearInterval(state.recording.timerId);
}

function pauseRecording() {
  if (state.mediaRecorder?.state !== "recording") return;
  state.mediaRecorder.pause();
  state.recording.status = "paused";
  state.recording.pausedAt = Date.now();
  updateRecordingPanel();
  showStatus("Recording paused", 40);
}

function resumeRecording() {
  if (state.mediaRecorder?.state !== "paused") return;
  state.mediaRecorder.resume();
  state.recording.pausedMs += Date.now() - state.recording.pausedAt;
  state.recording.pausedAt = null;
  state.recording.status = "recording";
  updateRecordingPanel();
  showStatus("Recording resumed", 40);
}

function stopRecording() {
  if (!state.mediaRecorder || state.recording?.status === "stopped") return;
  state.recording.durationMs = getActiveRecordingDuration();
  state.mediaRecorder.stop();
}

async function saveStoppedRecording() {
  if (!state.recording?.blob) {
    showError(new Error("No stopped recording is ready to save."));
    return;
  }

  try {
    elements.saveRecording.disabled = true;
    showStatus("Saving recording...", 85);
    const buffer = await state.recording.blob.arrayBuffer();
    const filePath = await window.mico360.saveRecording({ buffer, extension: "webm" });
    state.recording.filePath = filePath;
    state.recording.fileName = filePath.split(/[\\/]/).pop() || state.recording.fileName;
    updateRecordingPanel();
    showRecordingSummary("Saved and ready for transcription.");
    showStatus("Recording saved. Transcribing now...", 90);
    await handleFiles([filePath], { replaceTranscript: false });
    resetRecordingState("Recording saved and transcribed.");
  } catch (error) {
    showError(error);
    updateRecordingPanel();
  }
}

function cancelRecording() {
  if (!state.recording) return;
  if (state.mediaRecorder && state.recording.status !== "stopped") {
    state.recording.wasCancelled = true;
    state.mediaRecorder.stop();
    return;
  }
  resetRecordingState("Recording discarded.");
}

function showRecordingSummary(message) {
  const recording = state.recording;
  if (!recording) return;
  elements.recordingSummary.classList.remove("hidden");
  elements.recordingSummaryText.textContent = [
    message,
    `Duration: ${formatDuration(recording.durationMs || getActiveRecordingDuration())}`,
    `File: ${recording.fileName}`,
    "Format: WebM",
    `Size: ${formatFileSize(recording.bytes)}`,
    `Location: ${recording.filePath || "Not saved yet"}`
  ].join(" | ");
}

function resetRecordingState(message) {
  stopRecordingTimer();
  state.mediaRecorder = null;
  state.recordedChunks = [];
  state.recording?.stream?.getTracks().forEach((track) => track.stop());
  state.recording = null;
  elements.recordingPanel.classList.add("hidden");
  elements.recordingSummary.classList.add("hidden");
  elements.recordMic.disabled = false;
  elements.recordScreen.disabled = false;
  showStatus(message, 100);
}

function wireEvents() {
  elements.settingsToggle.addEventListener("click", () => elements.settingsPanel.classList.remove("hidden"));
  elements.settingsClose.addEventListener("click", () => elements.settingsPanel.classList.add("hidden"));
  elements.settingsSave.addEventListener("click", async () => {
    await persistSettings();
    showStatus("Settings saved", 100);
  });
  elements.newProject.addEventListener("click", () => {
    if (state.recording) resetRecordingState("Recording discarded.");
    state.currentProject = null;
    state.selectedFile = null;
    state.selectedFiles = [];
    elements.projectTitle.value = "";
    elements.selectedFile.textContent = "No file selected";
    elements.fileQueue.innerHTML = "";
    elements.transcriptInput.value = "";
    elements.minutesOutput.value = "";
    setDirty(false);
    showStatus("New project ready", 100);
  });
  elements.saveProjectTop.addEventListener("click", () => saveProject(true));
  $$(".settings-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$(".settings-tab").forEach((item) => item.classList.toggle("active", item === tab));
      $$(".settings-content").forEach((pane) => pane.classList.toggle("active", pane.dataset.pane === tab.dataset.tab));
    });
  });
  elements.themeToggle.addEventListener("click", async () => {
    document.documentElement.dataset.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    await persistSettings();
  });
  elements.refreshModels.addEventListener("click", loadModels);
  elements.checkUpdates.addEventListener("click", async () => {
    try {
      elements.checkUpdates.disabled = true;
      elements.updateStatus.textContent = "Checking for updates...";
      const result = await window.mico360.checkForUpdates();
      if (result?.message) elements.updateStatus.textContent = result.message;
    } catch (error) {
      elements.updateStatus.textContent = error.message || "Update check failed.";
      showError(error);
    } finally {
      elements.checkUpdates.disabled = false;
    }
  });
  elements.installUpdate.addEventListener("click", async () => {
    try {
      await window.mico360.installUpdate();
    } catch (error) {
      showError(error);
    }
  });
  elements.companyProfileSelect.addEventListener("change", async () => {
    const profile = getProfiles().find((item) => item.id === elements.companyProfileSelect.value);
    if (!profile) return;
    state.settings.activeCompanyProfileId = profile.id;
    loadProfileIntoForm(profile);
    await window.mico360.saveSettings({ activeCompanyProfileId: profile.id });
  });
  elements.newProfile.addEventListener("click", () => {
    const profile = {
      ...defaultCompanyProfile(),
      id: crypto.randomUUID(),
      name: "New Company Profile",
      companyName: "New Company"
    };
    state.settings.companyProfiles = upsertProfile(getProfiles(), profile);
    state.settings.activeCompanyProfileId = profile.id;
    renderCompanyProfiles();
    updateProfilePreview();
  });
  elements.saveProfile.addEventListener("click", () => saveActiveProfile());
  elements.deleteProfile.addEventListener("click", async () => {
    const profiles = getProfiles();
    if (profiles.length <= 1) {
      showError(new Error("Keep at least one company profile."));
      return;
    }
    if (!confirm("Delete this company profile?")) return;
    const activeId = elements.companyProfileSelect.value;
    const nextProfiles = profiles.filter((profile) => profile.id !== activeId);
    state.settings = await window.mico360.saveSettings({
      companyProfiles: nextProfiles,
      activeCompanyProfileId: nextProfiles[0].id
    });
    renderCompanyProfiles();
    updateProfilePreview();
    showStatus("Company profile deleted", 100);
  });
  elements.chooseLogo.addEventListener("click", async () => {
    try {
      const logoPath = await window.mico360.chooseLogo();
      if (!logoPath) return;
      elements.logoPath.value = logoPath;
      await saveActiveProfile("Logo attached to profile");
    } catch (error) {
      showError(error);
    }
  });
  elements.importProfiles.addEventListener("click", async () => {
    try {
      const imported = await window.mico360.importProfiles();
      if (!imported?.length) return;
      const profiles = [...imported, ...getProfiles()];
      state.settings = await window.mico360.saveSettings({
        ...getSettingsFromUi(),
        companyProfiles: profiles,
        activeCompanyProfileId: imported[0].id
      });
      renderCompanyProfiles();
      showStatus(`Imported ${imported.length} company profile(s)`, 100);
      showToast(`Imported ${imported.length} company profile(s).`);
    } catch (error) {
      showError(error);
    }
  });
  elements.exportProfile.addEventListener("click", async () => {
    try {
      await saveActiveProfile("Company profile saved for export");
      const filePath = await window.mico360.exportProfiles({
        format: elements.profileExportFormat.value,
        profiles: [getProfileFromUi()]
      });
      if (filePath) {
        showStatus(`Exported profile: ${filePath}`, 100);
        showToast(`Exported profile: ${filePath}`);
      }
    } catch (error) {
      showError(error);
    }
  });
  elements.exportAllProfiles.addEventListener("click", async () => {
    try {
      const profiles = currentProfilesWithActive();
      state.settings = await window.mico360.saveSettings({ ...getSettingsFromUi(), companyProfiles: profiles });
      const filePath = await window.mico360.exportProfiles({
        format: elements.profileExportFormat.value,
        profiles
      });
      if (filePath) {
        showStatus(`Exported profiles: ${filePath}`, 100);
        showToast(`Exported profiles: ${filePath}`);
      }
    } catch (error) {
      showError(error);
    }
  });
  [
    elements.companyProfileName,
    elements.companyName,
    elements.companyAddress,
    elements.contactPerson,
    elements.contactEmail,
    elements.contactPhone,
    elements.companyWebsite,
    elements.preparedBy,
    elements.classification,
    elements.includeLogo,
    elements.pdfFooter,
    elements.logoPosition,
    elements.logoWidth,
    elements.logoCustomX,
    elements.logoCustomY,
    elements.enablePageNumbers,
    elements.pageNumberPosition,
    elements.footerAlignment,
    elements.footerSpacing,
    elements.profileNotes
  ].forEach((input) => {
    input.addEventListener("input", updateProfilePreview);
    input.addEventListener("change", updateProfilePreview);
  });
  elements.modelSelect.addEventListener("change", async () => {
    updateSummaryLabels();
    await persistSettings();
  });
  elements.styleSelect.addEventListener("change", async () => {
    updateSummaryLabels();
    await persistSettings();
  });
  elements.applyPromptPreset.addEventListener("click", async () => {
    const [type, ...nameParts] = elements.promptPreset.value.split(":");
    const name = nameParts.join(":");
    if (type === "custom") {
      const custom = (state.settings.customPrompts || []).find((prompt) => prompt.name === name);
      elements.promptTemplate.value = custom?.template || elements.promptTemplate.value;
      elements.promptName.value = custom?.name || name;
    } else {
      elements.promptTemplate.value = window.mico360.constants.promptPresets[name] || window.mico360.constants.defaultPrompt;
      elements.promptName.value = name || "Default Meeting Minutes";
    }
    await persistSettings();
    showStatus("Prompt preset applied", 100);
  });
  elements.newPrompt.addEventListener("click", () => {
    elements.promptName.value = "";
    elements.promptTemplate.value = window.mico360.constants.defaultPrompt;
    elements.promptTemplate.focus();
    showStatus("New prompt ready", 100);
  });
  elements.savePrompt.addEventListener("click", async () => {
    const name = elements.promptName.value.trim();
    const template = elements.promptTemplate.value.trim();
    if (!name || !template) {
      showError(new Error("Enter a prompt name and prompt template before saving."));
      return;
    }
    if (!template.includes("[TRANSCRIPT_HERE]")) {
      showError(new Error("Prompt must include [TRANSCRIPT_HERE]."));
      return;
    }
    const prompts = [
      { name, template, updatedAt: new Date().toISOString() },
      ...(state.settings.customPrompts || []).filter((prompt) => prompt.name !== name)
    ].slice(0, 50);
    state.settings = await window.mico360.saveSettings({
      ...getSettingsFromUi(),
      customPrompts: prompts,
      activePromptName: name,
      promptTemplate: template
    });
    populatePromptPresets();
    elements.promptPreset.value = `custom:${name}`;
    renderPromptLibrary();
    showStatus("Prompt saved locally", 100);
  });
  elements.deletePrompt.addEventListener("click", async () => {
    const name = elements.promptName.value.trim();
    if (!name) {
      showError(new Error("Choose a saved prompt before deleting."));
      return;
    }
    if (!(state.settings.customPrompts || []).some((prompt) => prompt.name === name)) {
      showError(new Error("This prompt is a built-in preset or has not been saved yet."));
      return;
    }
    if (!confirm(`Delete prompt "${name}"?`)) return;
    const prompts = (state.settings.customPrompts || []).filter((prompt) => prompt.name !== name);
    state.settings = await window.mico360.saveSettings({
      ...getSettingsFromUi(),
      customPrompts: prompts,
      activePromptName: "",
      promptTemplate: window.mico360.constants.defaultPrompt
    });
    elements.promptName.value = "Default Meeting Minutes";
    elements.promptTemplate.value = window.mico360.constants.defaultPrompt;
    populatePromptPresets();
    renderPromptLibrary();
    showStatus("Prompt deleted", 100);
  });
  elements.resetPrompt.addEventListener("click", async () => {
    elements.promptTemplate.value = window.mico360.constants.defaultPrompt;
    elements.promptName.value = "Default Meeting Minutes";
    await persistSettings();
    showStatus("Prompt reset", 100);
  });
  elements.promptLibraryList.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-prompt-action]");
    if (!button) return;
    const name = button.dataset.promptName;
    const prompts = state.settings.customPrompts || [];
    const prompt = prompts.find((item) => item.name === name);
    if (button.dataset.promptAction === "view") {
      if (!prompt) return;
      elements.promptName.value = prompt.name;
      elements.promptTemplate.value = prompt.template;
      elements.promptPreset.value = `custom:${prompt.name}`;
      showStatus("Prompt loaded for editing", 100);
      return;
    }
    if (button.dataset.promptAction === "delete") {
      if (!confirm(`Delete prompt "${name}"?`)) return;
      state.settings = await window.mico360.saveSettings({
        ...getSettingsFromUi(),
        customPrompts: prompts.filter((item) => item.name !== name)
      });
      populatePromptPresets();
      showStatus("Prompt deleted", 100);
    }
  });
  elements.chooseFile.addEventListener("click", async () => handleFiles(await window.mico360.chooseFile()));
  elements.reTranscript.addEventListener("click", reTranscriptSelectedFiles);
  elements.generateBtn.addEventListener("click", generateMinutes);
  elements.copyBtn.addEventListener("click", async () => {
    await navigator.clipboard.writeText(elements.minutesOutput.value);
    showStatus("Minutes copied", 100);
  });
  elements.saveProject.addEventListener("click", () => saveProject(true));
  [elements.projectTitle, elements.transcriptInput, elements.minutesOutput].forEach((input) => {
    input.addEventListener("input", () => setDirty(true));
  });
  elements.recordMic.addEventListener("click", () => startRecording("mic"));
  elements.recordScreen.addEventListener("click", () => startRecording("screen"));
  elements.pauseRecording.addEventListener("click", pauseRecording);
  elements.resumeRecording.addEventListener("click", resumeRecording);
  elements.stopRecording.addEventListener("click", stopRecording);
  elements.saveRecording.addEventListener("click", saveStoppedRecording);
  elements.cancelRecording.addEventListener("click", cancelRecording);
  elements.historySearch.addEventListener("input", () => renderHistory(elements.historySearch.value));
  elements.historyList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-id]");
    if (!button) return;
    const project = state.projects.find((item) => item.id === button.dataset.id);
    if (!project) return;
    state.currentProject = project;
    elements.projectTitle.value = project.title || "";
    elements.modelSelect.value = project.model || elements.modelSelect.value;
    elements.styleSelect.value = project.style || elements.styleSelect.value;
    elements.transcriptInput.value = project.transcript || "";
    elements.minutesOutput.value = cleanMinutesText(project.minutes || "");
    updateSummaryLabels();
    setDirty(false);
    showStatus("Loaded local project", 100);
  });

  $$(".secondary[data-export]").forEach((button) => {
    button.addEventListener("click", () => exportCurrent(button.dataset.export));
  });

  ["dragenter", "dragover"].forEach((name) => {
    elements.dropZone.addEventListener(name, (event) => {
      event.preventDefault();
      elements.dropZone.classList.add("drag-over");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    elements.dropZone.addEventListener(name, (event) => {
      event.preventDefault();
      elements.dropZone.classList.remove("drag-over");
    });
  });
  elements.dropZone.addEventListener("drop", (event) => {
    handleFiles(Array.from(event.dataTransfer.files));
  });

  window.mico360.onProgress(({ stage, message, progress }) => {
    const compact = String(message).replace(/\s+/g, " ").trim();
    if (compact) {
      const fallback = stage === "ai" ? 65 : 45;
      showStatus(`${stage}: ${compact.slice(-180)}`, progress ?? fallback);
    }
  });
  window.mico360.onUpdate(({ status, message, percent }) => {
    elements.updateStatus.textContent = message || "Update status changed.";
    elements.installUpdate.disabled = status !== "ready";
    if (status === "downloading") setProgress(percent || 0, message || "Downloading update");
    if (status === "ready") showStatus("Update ready to install", 100);
  });
}

async function boot() {
  populateStyles();
  await loadSettings();
  await loadModels();
  await loadHistory();
  wireEvents();
}

boot().catch(showError);
