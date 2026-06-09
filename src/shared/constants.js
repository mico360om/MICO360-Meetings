const OUTPUT_STYLES = [
  "Formal Minutes",
  "Short Summary",
  "Detailed Minutes",
  "Action Item Report",
  "Executive Summary"
];

const DEFAULT_PROMPT = `You are a professional meeting secretary. Convert the following meeting transcript into accurate and formal minutes of meeting. Do not invent names, dates, decisions, or action items. If information is missing, write 'Not specified'. Organize the output clearly using headings and bullet points.

Transcript:
[TRANSCRIPT_HERE]`;

const PROMPT_PRESETS = {
  "Formal Minutes": `You are a professional meeting secretary. Convert the following meeting transcript into accurate formal minutes of meeting. Do not use markdown bold markers. Do not invent names, dates, decisions, or action items. If information is missing, write 'Not specified'. Use clear section headings and concise bullet points.

Transcript:
[TRANSCRIPT_HERE]`,
  "Executive Summary": `Create an executive-ready meeting summary from the transcript. Keep it brief, decision-focused, and professional. Do not use markdown bold markers. Do not invent missing details; write 'Not specified' where needed.

Transcript:
[TRANSCRIPT_HERE]`,
  "Action Item Report": `Extract action items from the transcript and organize them into a practical follow-up report. Include task, responsible person, deadline, status, and dependencies. Do not use markdown bold markers. Do not invent missing details.

Transcript:
[TRANSCRIPT_HERE]`,
  "Detailed Minutes": `Convert the transcript into detailed meeting minutes with clear sections, decisions, risks, pending issues, and next meeting notes. Do not use markdown bold markers. Do not invent missing details.

Transcript:
[TRANSCRIPT_HERE]`
};

const MINUTES_SECTIONS = [
  "Meeting Title",
  "Date and Time",
  "Attendees",
  "Purpose of Meeting",
  "Meeting Summary",
  "Key Discussion Points",
  "Decisions Made",
  "Action Items",
  "Pending Issues",
  "Risks or Concerns",
  "Next Meeting Notes",
  "Closing Summary"
];

module.exports = { OUTPUT_STYLES, DEFAULT_PROMPT, PROMPT_PRESETS, MINUTES_SECTIONS };
