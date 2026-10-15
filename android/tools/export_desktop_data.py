"""Export the desktop app's shared text data for the Android app.

    <desktop python> android/tools/export_desktop_data.py

Writes android/src/core/data/desktop.json from the desktop modules, so prompts,
styles, meeting types, PDF designs and the Cloud endpoint are never copied by hand.
LOCALAPPDATA points at a temp folder first: the real user data is never touched.
"""
import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="mico360_export_")
sys.path.insert(0, str(REPO))

from mico360 import config  # noqa: E402
from mico360.core import cleaning, insights, meeting_types, profiles, prompts, tasks  # noqa: E402
from mico360.export import pdf_designs, pdf_export  # noqa: E402

data = {
    "transcriptToken": prompts.TRANSCRIPT_TOKEN,
    "minutesStructure": prompts.MINUTES_STRUCTURE,
    "baseTemplate": prompts.BASE_TEMPLATE,
    "outputStyles": [[k, v] for k, v in prompts.OUTPUT_STYLES.items()],
    "defaultStyle": prompts.DEFAULT_STYLE,
    "chunkSummaryPrompt": prompts.CHUNK_SUMMARY_PROMPT,
    "reduceNotesPrefix": prompts.REDUCE_NOTES_PREFIX,
    "mergeNotesPrompt": prompts.MERGE_NOTES_PROMPT,
    "builtinPrompts": [list(t) for t in prompts.BUILTIN_PROMPTS],
    "promptCategories": prompts.PROMPT_CATEGORIES,
    "meetingTypes": [asdict(t) for t in meeting_types.BUILTIN_TYPES],
    "fillers": cleaning.FILLERS,
    "stutterWords": cleaning._STUTTER_WORDS,
    "statusCycle": tasks.STATUS_CYCLE,
    "statusSynonyms": tasks._STATUS_SYNONYMS,
    "prioritySynonyms": tasks._PRIORITY_SYNONYMS,
    "placeholders": sorted(tasks._PLACEHOLDER),
    "insightsStopwords": sorted(insights._STOPWORDS),
    "arabicMarks": [cp for cp in range(0x0600, 0x0700) if insights._AR_MARKS_RE.match(chr(cp))],
    # Python's str.casefold() where it differs from lower(): [code point, folded code points...]
    "casefoldExtra": [[cp, *map(ord, chr(cp).casefold())] for cp in range(0x110000)
                      if not 0xD800 <= cp <= 0xDFFF and chr(cp).casefold() != chr(cp).lower()],
    "statusOrder": insights.STATUS_ORDER,
    "pdfDesigns": [asdict(d) for d in pdf_designs.choices()],
    "defaultPdfDesign": profiles.DEFAULT_PDF_DESIGN,
    "profileDefaults": asdict(profiles.CompanyProfile()),
    "statusColors": pdf_export.STATUS_COLORS,
    "pdfTitleKeys": sorted(pdf_export._TITLE_KEYS),
    "pdfPeopleKeys": sorted(pdf_export._PEOPLE_KEYS),
    "pdfStatusHeaders": sorted(pdf_export._STATUS_HEADERS),
    "pdfPlaceholders": sorted(pdf_export._PLACEHOLDERS),
    "signaturesEn": list(pdf_export._SIGN_EN),
    "signaturesAr": list(pdf_export._SIGN_AR),
    "connect": {
        "baseUrl": config.MICO360_CONNECT_BASE_URL,
        "httpsCandidates": list(config.MICO360_CONNECT_HTTPS_CANDIDATES),
        "defaultModel": config.MICO360_CONNECT_DEFAULT_MODEL,
    },
}
out = HERE.parent / "src" / "core" / "data" / "desktop.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {out}")
