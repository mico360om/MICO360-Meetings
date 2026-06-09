const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");
const bundledFfmpegPath = require("ffmpeg-static");
const { Document, Packer, Paragraph, TextRun } = require("docx");
const { PDFDocument, StandardFonts, rgb } = require("pdf-lib");
const JSZip = require("jszip");
const { DEFAULT_PROMPT, MINUTES_SECTIONS } = require("../shared/constants");

const AUDIO_EXTENSIONS = new Set([".mp3", ".wav", ".m4a"]);
const VIDEO_EXTENSIONS = new Set([".mp4", ".mov", ".mkv", ".webm"]);
const PROFILE_FIELDS = [
  "name",
  "companyName",
  "address",
  "contactPerson",
  "email",
  "phone",
  "website",
  "registrationNumber",
  "preparedBy",
  "classification",
  "pdfFooter",
  "includeLogo",
  "logoPath",
  "logoPosition",
  "logoWidth",
  "logoCustomX",
  "logoCustomY",
  "enablePageNumbers",
  "pageNumberPosition",
  "footerAlignment",
  "footerSpacing",
  "notes"
];

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function appendLog(baseDir, message, details = "") {
  const logDir = path.join(baseDir, "logs");
  ensureDir(logDir);
  const line = `[${new Date().toISOString()}] ${message}${details ? `\n${details}` : ""}\n`;
  fs.appendFileSync(path.join(logDir, "mico360-meetings.log"), line, "utf8");
}

function detectMediaType(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (AUDIO_EXTENSIONS.has(ext)) return "audio";
  if (VIDEO_EXTENSIONS.has(ext)) return "video";
  if (ext === ".txt" || ext === ".md") return "transcript";
  return "unknown";
}

function runCommand(command, args, options, onProgress) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true, ...options });
    let stdout = "";
    let stderr = "";

    child.stdout?.on("data", (data) => {
      const text = data.toString();
      stdout += text;
      onProgress?.(text);
    });

    child.stderr?.on("data", (data) => {
      const text = data.toString();
      stderr += text;
      onProgress?.(text);
    });

    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve({ stdout, stderr });
      else reject(new Error(`${command} exited with code ${code}\n${stderr}`));
    });
  });
}

function looksLikeMissingPython(error) {
  const text = `${error.message || ""}\n${error.stack || ""}`;
  return /Python was not found|Microsoft Store|App execution aliases|No Python|can't find a default Python|No suitable Python|Python 3\.10 or newer is required/i.test(text);
}

function looksLikeMissingFasterWhisper(error) {
  const text = `${error.message || ""}\n${error.stack || ""}`;
  return /faster-whisper is not installed|No module named ['"]?faster_whisper|ModuleNotFoundError/i.test(text);
}

function readInstallerConfig() {
  try {
    const localAppData = process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
    const configPath = path.join(localAppData, "MICO360 Meetings", "config", "installer-config.json");
    if (!fs.existsSync(configPath)) return null;
    return JSON.parse(fs.readFileSync(configPath, "utf8"));
  } catch {
    return null;
  }
}

function getPythonTranscriptionAttempts() {
  const attempts = [];
  const config = readInstallerConfig();
  if (config?.pythonExecutable && fs.existsSync(config.pythonExecutable)) {
    attempts.push({
      command: config.pythonExecutable,
      args: [],
      label: "MICO360 Python environment"
    });
  }
  attempts.push(
    { command: "py", args: ["-3"], label: "Python launcher" },
    { command: "python3", args: [], label: "python3" },
    { command: "python", args: [], label: "python" }
  );
  return attempts;
}

async function runCommandWithFriendlyMissing(command, args, options, onProgress, missingMessage) {
  try {
    return await runCommand(command, args, options, onProgress);
  } catch (error) {
    if (error.code === "ENOENT") {
      throw new Error(missingMessage || `${command} was not found. Make sure it is installed and available in PATH.`);
    }
    throw error;
  }
}

function getFfmpegCommand(settings = {}) {
  return settings.ffmpegPath || bundledFfmpegPath || "ffmpeg";
}

async function convertToWav(inputPath, outputDir, settings = {}, onProgress) {
  ensureDir(outputDir);
  const mediaType = detectMediaType(inputPath);
  if (mediaType === "audio" && path.extname(inputPath).toLowerCase() === ".wav") {
    return inputPath;
  }

  const outputPath = path.join(outputDir, `${path.basename(inputPath, path.extname(inputPath))}-${Date.now()}.wav`);
  await runCommand(getFfmpegCommand(settings), [
    "-y",
    "-i",
    inputPath,
    "-vn",
    "-acodec",
    "pcm_s16le",
    "-ar",
    "16000",
    "-ac",
    "1",
    outputPath
  ], {}, onProgress);
  return outputPath;
}

async function transcribeWithFasterWhisperPython({ inputPath, workDir, model, language, onProgress }) {
  const script = [
    "import sys",
    "if sys.version_info < (3, 10):",
    "    raise SystemExit('Python 3.10 or newer is required for faster-whisper transcription.')",
    "import pathlib",
    "try:",
    "    from faster_whisper import WhisperModel",
    "except Exception as exc:",
    "    raise SystemExit('Python package faster-whisper is not installed. Install it with: pip install faster-whisper') from exc",
    "audio_path, out_dir, model_name, language = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] or None",
    "pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)",
    "model = WhisperModel(model_name, device='cpu', compute_type='int8')",
    "segments, info = model.transcribe(audio_path, language=language, vad_filter=True)",
    "out_path = pathlib.Path(out_dir) / 'transcript.txt'",
    "with out_path.open('w', encoding='utf-8') as handle:",
    "    for segment in segments:",
    "        text = segment.text.strip()",
    "        if text:",
    "            print(text, flush=True)",
    "            handle.write(text + '\\n')",
    "print(str(out_path), flush=True)"
  ].join("\n");

  const setupErrors = [];
  for (const attempt of getPythonTranscriptionAttempts()) {
    try {
      onProgress?.(`Trying ${attempt.label} for faster-whisper transcription...\n`);
      await runCommandWithFriendlyMissing(
        attempt.command,
        [...attempt.args, "-c", script, inputPath, workDir, model, language || ""],
        {},
        onProgress,
        `${attempt.label} was not found.`
      );
      return fs.readFileSync(path.join(workDir, "transcript.txt"), "utf8");
    } catch (error) {
      if (looksLikeMissingPython(error) || looksLikeMissingFasterWhisper(error)) {
        setupErrors.push(`${attempt.label}: ${error.message}`);
      } else {
        throw error;
      }
    }
  }
  throw new Error([
    "faster-whisper is not installed in any supported Python environment.",
    "Run the MICO360 Meetings prerequisites script again from the installed resources folder, then reopen the app.",
    "Details:",
    ...setupErrors
  ].join("\n"));
}

async function transcribeWithLocalTool({ inputPath, settings, workDir, onProgress }) {
  const engine = settings.transcriptionEngine || "faster-whisper";
  const model = settings.whisperModel || "base";
  const languageArgs = settings.language ? ["--language", settings.language] : [];
  ensureDir(workDir);

  if (engine === "whisper.cpp") {
    const whisperCppPath = settings.whisperCppPath || "whisper-cli";
    const whisperCppModel = settings.whisperCppModelPath || "";
    if (!whisperCppModel) {
      throw new Error("whisper.cpp model path is required when using whisper.cpp.");
    }
    const outputBase = path.join(workDir, `transcript-${Date.now()}`);
    await runCommandWithFriendlyMissing(
      whisperCppPath,
      ["-m", whisperCppModel, "-f", inputPath, "-otxt", "-of", outputBase],
      {},
      onProgress,
      "whisper.cpp executable was not found. Install whisper.cpp and make sure whisper-cli is in PATH."
    );
    return fs.readFileSync(`${outputBase}.txt`, "utf8");
  }

  if (engine === "openai-whisper") {
    await runCommandWithFriendlyMissing(
      "whisper",
      [inputPath, "--model", model, "--output_format", "txt", "--output_dir", workDir, ...languageArgs],
      {},
      onProgress,
      "OpenAI Whisper CLI was not found. Install it with: pip install -U openai-whisper"
    );
    const outPath = path.join(workDir, `${path.basename(inputPath, path.extname(inputPath))}.txt`);
    return fs.readFileSync(outPath, "utf8");
  }

  try {
    await runCommandWithFriendlyMissing(
      "faster-whisper",
      [inputPath, "--model", model, "--output_dir", workDir, "--output_format", "txt", ...languageArgs],
      {},
      onProgress,
      ""
    );
  } catch (error) {
    if (error.message.includes("faster-whisper was not found") || error.code === "ENOENT") {
      return transcribeWithFasterWhisperPython({
        inputPath,
        workDir,
        model,
        language: settings.language,
        onProgress
      });
    }
    throw error;
  }
  const txtFile = fs.readdirSync(workDir)
    .filter((name) => name.endsWith(".txt"))
    .map((name) => path.join(workDir, name))
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs)[0];
  if (!txtFile) throw new Error("Transcription completed, but no transcript file was produced.");
  return fs.readFileSync(txtFile, "utf8");
}

function cleanTranscript(transcript) {
  const fillerPattern = /\b(um+|uh+|erm+|ah+|like|you know|sort of|kind of)\b/gi;
  const lines = transcript
    .replace(fillerPattern, "")
    .replace(/[ \t]+/g, " ")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  const cleaned = [];
  for (const line of lines) {
    if (cleaned[cleaned.length - 1] !== line) cleaned.push(line);
  }
  return cleaned.join("\n");
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

function normalizeCompanyProfile(input = {}) {
  return {
    id: input.id || crypto.randomUUID(),
    name: input.name || input.companyName || "Company Profile",
    companyName: input.companyName || input.name || "Company",
    address: input.address || "",
    contactPerson: input.contactPerson || "",
    email: input.email || input.contactEmail || "",
    phone: input.phone || "",
    website: input.website || "",
    registrationNumber: input.registrationNumber || input.taxId || "",
    preparedBy: input.preparedBy || "MICO360 Meetings",
    classification: input.classification || "Confidential",
    pdfFooter: input.pdfFooter || input.footer || "Generated locally by MICO360 Meetings",
    includeLogo: input.includeLogo !== false && input.includeLogo !== "false",
    logoPath: input.logoPath || "",
    logoPosition: input.logoPosition || "left",
    logoWidth: Number(input.logoWidth || 124),
    logoCustomX: Number(input.logoCustomX || 48),
    logoCustomY: Number(input.logoCustomY || 728),
    enablePageNumbers: input.enablePageNumbers !== false && input.enablePageNumbers !== "false",
    pageNumberPosition: input.pageNumberPosition || "footer-right",
    footerAlignment: input.footerAlignment || "left",
    footerSpacing: Number(input.footerSpacing || 30),
    notes: input.notes || ""
  };
}

function profilesToRows(profiles) {
  return profiles.map((profile) => {
    const normalized = normalizeCompanyProfile(profile);
    return Object.fromEntries(PROFILE_FIELDS.map((field) => [field, normalized[field] ?? ""]));
  });
}

function parseCsv(content) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < content.length; index += 1) {
    const char = content[index];
    const next = content[index + 1];
    if (quoted && char === "\"" && next === "\"") {
      cell += "\"";
      index += 1;
    } else if (char === "\"") {
      quoted = !quoted;
    } else if (char === "," && !quoted) {
      row.push(cell);
      cell = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && next === "\n") index += 1;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += char;
    }
  }
  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((item) => item.some((value) => String(value).trim()));
}

function csvEscape(value) {
  const text = String(value ?? "");
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, "\"\"")}"` : text;
}

function rowsToCsv(rows) {
  const header = PROFILE_FIELDS;
  return [
    header.map(csvEscape).join(","),
    ...rows.map((row) => header.map((field) => csvEscape(row[field])).join(","))
  ].join("\n");
}

function xmlEscape(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function columnName(index) {
  let name = "";
  let current = index + 1;
  while (current > 0) {
    const remainder = (current - 1) % 26;
    name = String.fromCharCode(65 + remainder) + name;
    current = Math.floor((current - remainder) / 26);
  }
  return name;
}

async function rowsToXlsxBuffer(rows) {
  const zip = new JSZip();
  const allRows = [PROFILE_FIELDS, ...rows.map((row) => PROFILE_FIELDS.map((field) => row[field] ?? ""))];
  const sheetRows = allRows.map((row, rowIndex) => {
    const cells = row.map((value, colIndex) => {
      const ref = `${columnName(colIndex)}${rowIndex + 1}`;
      return `<c r="${ref}" t="inlineStr"><is><t>${xmlEscape(value)}</t></is></c>`;
    }).join("");
    return `<row r="${rowIndex + 1}">${cells}</row>`;
  }).join("");

  zip.file("[Content_Types].xml", `<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>`);
  zip.folder("_rels").file(".rels", `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`);
  zip.folder("xl").file("workbook.xml", `<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Company Profiles" sheetId="1" r:id="rId1"/></sheets></workbook>`);
  zip.folder("xl").folder("_rels").file("workbook.xml.rels", `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>`);
  zip.folder("xl").folder("worksheets").file("sheet1.xml", `<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${sheetRows}</sheetData></worksheet>`);
  return zip.generateAsync({ type: "nodebuffer" });
}

function stripXml(text) {
  return text.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

async function xlsxToProfiles(filePath) {
  const zip = await JSZip.loadAsync(fs.readFileSync(filePath));
  const sheet = await zip.file("xl/worksheets/sheet1.xml")?.async("string");
  if (!sheet) throw new Error("The Excel file does not contain a readable first worksheet.");
  const rowMatches = [...sheet.matchAll(/<row[^>]*>([\s\S]*?)<\/row>/g)].map((match) => match[1]);
  const rows = rowMatches.map((rowXml) => {
    return [...rowXml.matchAll(/<c[^>]*>([\s\S]*?)<\/c>/g)].map((cellMatch) => {
      const valueMatch = cellMatch[1].match(/<t[^>]*>([\s\S]*?)<\/t>|<v[^>]*>([\s\S]*?)<\/v>/);
      return valueMatch ? stripXml(valueMatch[1] || valueMatch[2] || "") : "";
    });
  });
  const header = rows.shift() || [];
  return rows.map((row) => normalizeCompanyProfile(Object.fromEntries(header.map((field, index) => [field, row[index] || ""]))));
}

async function docxToProfile(filePath) {
  const zip = await JSZip.loadAsync(fs.readFileSync(filePath));
  const documentXml = await zip.file("word/document.xml")?.async("string");
  const text = documentXml ? stripXml(documentXml) : "";
  return normalizeCompanyProfile({
    name: path.basename(filePath, path.extname(filePath)),
    companyName: path.basename(filePath, path.extname(filePath)),
    notes: text.slice(0, 4000)
  });
}

async function importCompanyProfiles(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".json") {
    const parsed = JSON.parse(fs.readFileSync(filePath, "utf8"));
    const profiles = Array.isArray(parsed) ? parsed : parsed.companyProfiles || parsed.profiles || [parsed];
    return profiles.map(normalizeCompanyProfile);
  }
  if (ext === ".csv") {
    const rows = parseCsv(fs.readFileSync(filePath, "utf8"));
    const header = rows.shift() || [];
    return rows.map((row) => normalizeCompanyProfile(Object.fromEntries(header.map((field, index) => [field, row[index] || ""]))));
  }
  if (ext === ".xlsx") {
    return xlsxToProfiles(filePath);
  }
  if (ext === ".docx") {
    return [await docxToProfile(filePath)];
  }
  if (ext === ".pdf") {
    return [normalizeCompanyProfile({
      name: path.basename(filePath, ext),
      companyName: path.basename(filePath, ext),
      notes: `Imported PDF source: ${filePath}. PDF text extraction is not available in this local importer; review and complete profile fields manually.`
    })];
  }
  throw new Error("Unsupported profile import format. Use JSON, CSV, XLSX, DOCX, or PDF.");
}

function profileToText(profile) {
  const normalized = normalizeCompanyProfile(profile);
  return PROFILE_FIELDS.map((field) => `${field}: ${normalized[field] ?? ""}`).join("\n");
}

async function exportCompanyProfiles({ format, profiles, destinationPath }) {
  ensureDir(path.dirname(destinationPath));
  const normalized = profiles.map(normalizeCompanyProfile);
  const rows = profilesToRows(normalized);
  if (format === "json") {
    fs.writeFileSync(destinationPath, JSON.stringify(normalized, null, 2), "utf8");
    return destinationPath;
  }
  if (format === "csv") {
    fs.writeFileSync(destinationPath, rowsToCsv(rows), "utf8");
    return destinationPath;
  }
  if (format === "xlsx") {
    fs.writeFileSync(destinationPath, await rowsToXlsxBuffer(rows));
    return destinationPath;
  }
  if (format === "docx") {
    const children = normalized.flatMap((profile) => textToDocxParagraphs(`${profile.companyName}\n${profileToText(profile)}\n`));
    fs.writeFileSync(destinationPath, await Packer.toBuffer(new Document({ sections: [{ children }] })));
    return destinationPath;
  }
  if (format === "pdf") {
    const firstProfile = normalized[0] || normalizeCompanyProfile();
    fs.writeFileSync(destinationPath, await buildBrandedPdf(normalized.map(profileToText).join("\n\n"), firstProfile));
    return destinationPath;
  }
  throw new Error("Unsupported profile export format.");
}

function chunkTranscript(text, maxChars = 9000) {
  const paragraphs = text.split(/\n{2,}|\r?\n/).filter(Boolean);
  const chunks = [];
  let current = "";

  for (const paragraph of paragraphs) {
    if ((current + "\n" + paragraph).length > maxChars && current) {
      chunks.push(current.trim());
      current = paragraph;
    } else {
      current = `${current}\n${paragraph}`.trim();
    }
  }
  if (current) chunks.push(current.trim());
  return chunks.length ? chunks : [text];
}

async function fetchOllamaModels() {
  const response = await fetch("http://127.0.0.1:11434/api/tags");
  if (!response.ok) throw new Error(`Ollama returned HTTP ${response.status}`);
  const data = await response.json();
  return (data.models || []).map((model) => model.name);
}

async function callOllama({ model, prompt, onProgress }) {
  const response = await fetch("http://127.0.0.1:11434/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, prompt, stream: true, options: { temperature: 0.2 } })
  });

  if (!response.ok || !response.body) {
    const errorBody = await response.text().catch(() => "");
    throw new Error(`Ollama generation failed with HTTP ${response.status}. ${errorBody || "Is Ollama running and is the model installed?"}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let output = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    const text = decoder.decode(value, { stream: true });
    for (const line of text.split(/\n/).filter(Boolean)) {
      const packet = JSON.parse(line);
      if (packet.response) {
        output += packet.response;
        onProgress?.(packet.response);
      }
      if (packet.error) throw new Error(packet.error);
    }
  }
  return output.trim();
}

function buildPrompt({ transcript, style, template }) {
  const sectionList = MINUTES_SECTIONS.map((section) => `- ${section}`).join("\n");
  const styleInstruction = `Output style: ${style}.\nDo not use markdown bold markers such as **text**. Use plain text headings and bullet points.\nUse this required structure where appropriate:\n${sectionList}\nFor Action Items, include Task, Responsible Person, Deadline, and Status.`;
  const base = (template || DEFAULT_PROMPT).replace("[TRANSCRIPT_HERE]", transcript);
  return `${styleInstruction}\n\n${base}`;
}

async function generateMinutes({ transcript, model, style, promptTemplate, onProgress }) {
  const cleaned = cleanTranscript(transcript);
  const chunks = chunkTranscript(cleaned);

  if (chunks.length === 1) {
    return callOllama({ model, prompt: buildPrompt({ transcript: cleaned, style, template: promptTemplate }), onProgress });
  }

  const summaries = [];
  for (let index = 0; index < chunks.length; index += 1) {
    onProgress?.(`\n\n[Chunk ${index + 1}/${chunks.length}]\n`);
    const prompt = `Summarize this meeting transcript chunk faithfully. Do not invent details. Mark missing or unclear information as Not specified.\n\n${chunks[index]}`;
    summaries.push(await callOllama({ model, prompt, onProgress }));
  }

  const combined = summaries.map((summary, index) => `Chunk ${index + 1} summary:\n${summary}`).join("\n\n");
  return callOllama({ model, prompt: buildPrompt({ transcript: combined, style, template: promptTemplate }), onProgress });
}

function textToDocxParagraphs(text) {
  return text.split(/\r?\n/).map((line) => new Paragraph({
    children: [new TextRun({ text: line || " ", bold: /^#{1,3}\s|^[A-Z][A-Za-z ]+:$/.test(line) })],
    spacing: { after: 120 }
  }));
}

function escapePdfText(text) {
  return String(text).replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
}

function wrapText(text, maxChars = 92) {
  const lines = [];
  for (const rawLine of text.split(/\r?\n/)) {
    const words = rawLine.split(/\s+/).filter(Boolean);
    if (!words.length) {
      lines.push("");
      continue;
    }
    let current = "";
    for (const word of words) {
      const next = current ? `${current} ${word}` : word;
      if (next.length > maxChars && current) {
        lines.push(current);
        current = word;
      } else {
        current = next;
      }
    }
    if (current) lines.push(current);
  }
  return lines;
}

async function buildBrandedPdf(content, profile = {}) {
  const pdf = await PDFDocument.create();
  const regular = await pdf.embedFont(StandardFonts.Helvetica);
  const bold = await pdf.embedFont(StandardFonts.HelveticaBold);
  const pageSize = [612, 792];
  const margin = 54;
  const brand = rgb(0.61, 0.08, 0.09);
  const brandSoft = rgb(0.98, 0.94, 0.94);
  const paper = rgb(1, 1, 1);
  const rule = rgb(0.86, 0.88, 0.90);
  const ink = rgb(0.10, 0.11, 0.12);
  const muted = rgb(0.42, 0.46, 0.50);
  const company = profile.companyName || "MICO360";
  const preparedBy = profile.preparedBy || "MICO360 Meetings";
  const classification = profile.classification || "Confidential";
  const footer = profile.pdfFooter || profile.footer || "Generated locally by MICO360 Meetings";
  const includeLogo = profile.includeLogo !== false;
  const logoPath = profile.logoPath || path.join(__dirname, "../../assets/logo.png");
  const logoPosition = profile.logoPosition || "left";
  const logoWidth = Math.max(56, Math.min(210, Number(profile.logoWidth || 124)));
  const footerSpacing = Math.max(24, Math.min(64, Number(profile.footerSpacing || 30)));
  const footerAlignment = profile.footerAlignment || "left";
  const pageNumberPosition = profile.pageNumberPosition || "footer-right";
  const enablePageNumbers = profile.enablePageNumbers !== false;

  let logo = null;
  if (includeLogo && fs.existsSync(logoPath)) {
    const bytes = fs.readFileSync(logoPath);
    logo = /\.jpe?g$/i.test(logoPath)
      ? await pdf.embedJpg(bytes).catch(() => null)
      : await pdf.embedPng(bytes).catch(() => null);
  }

  const lines = wrapText(cleanMinutesText(content), 92);
  let page;
  let y;
  let pageNumber = 0;

  const addPage = () => {
    page = pdf.addPage(pageSize);
    pageNumber += 1;
    const { width, height } = page.getSize();
    page.drawRectangle({ x: 0, y: 0, width, height, color: paper });
    page.drawRectangle({ x: 0, y: height - 112, width, height: 112, color: brandSoft });
    page.drawRectangle({ x: 0, y: height - 116, width, height: 4, color: brand });
    page.drawLine({ start: { x: margin, y: 78 }, end: { x: width - margin, y: 78 }, thickness: 0.6, color: rule });

    let titleX = margin;
    let titleY = height - 46;
    if (logo) {
      const scaled = logo.scale(logoWidth / logo.width);
      const safeLogoHeight = Math.min(54, scaled.height);
      const safeLogoWidth = scaled.width * (safeLogoHeight / scaled.height);
      const logoY = logoPosition === "custom"
        ? Math.max(height - 104, Math.min(height - 38, Number(profile.logoCustomY || height - 72)))
        : height - 76;
      let logoX = margin;
      if (logoPosition === "center" || logoPosition === "top") logoX = (width - safeLogoWidth) / 2;
      if (logoPosition === "right") logoX = width - margin - safeLogoWidth;
      if (logoPosition === "custom") logoX = Math.max(margin, Math.min(width - margin - safeLogoWidth, Number(profile.logoCustomX || margin)));
      page.drawImage(logo, { x: logoX, y: logoY, width: safeLogoWidth, height: safeLogoHeight });
      if (logoPosition === "left") titleX = Math.min(margin + safeLogoWidth + 24, 260);
      if (logoPosition === "top" || logoPosition === "center") titleY = height - 92;
    }

    const classificationWidth = bold.widthOfTextAtSize(classification, 9);
    page.drawText(company.slice(0, 42), { x: titleX, y: titleY, size: 18, font: bold, color: ink });
    page.drawText("Meeting Minutes", { x: titleX, y: titleY - 19, size: 10, font: regular, color: muted });
    page.drawText(classification, { x: width - margin - classificationWidth, y: height - 42, size: 9, font: bold, color: brand });

    const footerText = footer.slice(0, 120);
    const footerWidth = regular.widthOfTextAtSize(footerText, 8);
    let footerX = margin;
    if (footerAlignment === "center") footerX = (width - footerWidth) / 2;
    if (footerAlignment === "right") footerX = width - margin - footerWidth;
    page.drawText(footerText, { x: footerX, y: footerSpacing, size: 8, font: regular, color: muted });
    if (enablePageNumbers) {
      const pageText = `Page ${pageNumber}`;
      const pageTextWidth = regular.widthOfTextAtSize(pageText, 8);
      let pageX = width - margin - pageTextWidth;
      let pageY = footerSpacing;
      if (pageNumberPosition.includes("header")) pageY = height - 76;
      if (pageNumberPosition.includes("left")) pageX = margin;
      if (pageNumberPosition.includes("center")) pageX = (width - pageTextWidth) / 2;
      if (pageNumberPosition.includes("right")) pageX = width - margin - pageTextWidth;
      page.drawText(pageText, { x: pageX, y: pageY, size: 8, font: regular, color: muted });
    }
    y = height - 148;
  };

  addPage();
  page.drawText(`Prepared by: ${preparedBy}`, { x: margin, y, size: 9, font: regular, color: muted });
  y -= 18;
  page.drawLine({ start: { x: margin, y }, end: { x: 612 - margin, y }, thickness: 0.6, color: rule });
  y -= 22;

  for (const line of lines) {
    if (y < 92) addPage();
    const isHeading = line && !line.startsWith("-") && !line.startsWith("|") && line.length < 48 && /^[A-Z0-9][A-Za-z0-9 &:/.-]+$/.test(line);
    const bullet = line.trim().startsWith("-") || line.trim().startsWith("*");
    const text = bullet ? line.replace(/^[-*]\s*/, "- ") : line;
    page.drawText(text || " ", {
      x: bullet ? margin + 12 : margin,
      y,
      size: isHeading ? 12 : 9.5,
      font: isHeading ? bold : regular,
      color: isHeading ? brand : ink
    });
    y -= isHeading ? 18 : 14;
  }

  return Buffer.from(await pdf.save());
}

async function exportMinutes({ format, content, destinationPath, profile }) {
  ensureDir(path.dirname(destinationPath));
  const cleanContent = cleanMinutesText(content);

  if (format === "txt") {
    fs.writeFileSync(destinationPath, cleanContent, "utf8");
    return destinationPath;
  }

  if (format === "docx") {
    const doc = new Document({ sections: [{ children: textToDocxParagraphs(cleanContent) }] });
    const buffer = await Packer.toBuffer(doc);
    fs.writeFileSync(destinationPath, buffer);
    return destinationPath;
  }

  if (format === "pdf") {
    fs.writeFileSync(destinationPath, await buildBrandedPdf(cleanContent, profile));
    return destinationPath;
  }

  throw new Error(`Unsupported export format: ${format}`);
}

function getDefaultProjectName() {
  return `Meeting ${new Date().toISOString().slice(0, 19).replace(/[T:]/g, "-")}`;
}

function createProjectRecord(input) {
  return {
    id: crypto.randomUUID(),
    title: input.title || getDefaultProjectName(),
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    sourceType: input.sourceType || "transcript",
    model: input.model || "",
    style: input.style || "Formal Minutes",
    transcript: input.transcript || "",
    minutes: input.minutes || ""
  };
}

function saveBufferToFile(buffer, extension, baseDir) {
  const uploads = path.join(baseDir, "recordings");
  ensureDir(uploads);
  const output = path.join(uploads, `recording-${Date.now()}.${extension}`);
  fs.writeFileSync(output, Buffer.from(buffer));
  return output;
}

module.exports = {
  appendLog,
  cleanTranscript,
  cleanMinutesText,
  convertToWav,
  createProjectRecord,
  detectMediaType,
  exportMinutes,
  exportCompanyProfiles,
  fetchOllamaModels,
  generateMinutes,
  importCompanyProfiles,
  normalizeCompanyProfile,
  saveBufferToFile,
  transcribeWithLocalTool
};
