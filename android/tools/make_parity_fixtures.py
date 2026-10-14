"""Run the desktop Python code on a fixed input set and write the expected
results to android/test/fixtures/*.json for the TypeScript parity tests.

    <desktop python> android/tools/make_parity_fixtures.py
"""
import base64
import json
import os
import sys
import tempfile
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="mico360_fixtures_")
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
from mico360.core import calendar_ics, cleaning, diarization, insights, profiles, prompts, tasks, updater  # noqa: E402
from mico360.export import md_blocks, rtl  # noqa: E402

OUT = HERE.parent / "test" / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)


def dump(name, obj):
    (OUT / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


MD = [
    "# Meeting Minutes\n**Meeting Title:** Budget review\n**Date and Time:** 5 Oct 2026\n**Attendees:** Ali, Sara\n\n## Purpose of Meeting\nPlan Q4.\nSecond line.",
    "## Action Items\n| Task | Responsible Person | Deadline | Status |\n| --- | --- | --- | --- |\n| Send report | Ali | 10 Oct | Pending |\n| Fix `a|b` pipe | Sara | 2026-10-12 | Done |\n| Escaped \\| pipe | Omar | | In progress |",
    "Task | Owner\n---|---\nNo leading pipe | Me\n\nAfter table",
    "- one\n- **two** bold\n* three\n\n1. first\n2) second\n7. seventh\n\n---\n\nPara with **bold** and `code`.",
    "### Deep ###\n#### Deeper\n###### Six\n####### Seven is text",
    "# محضر الاجتماع\n**عنوان الاجتماع:** الميزانية\n- بند أول\n| المهمة | المسؤول | الحالة |\n| --- | --- | --- |\n| إرسال التقرير | علي | قيد التنفيذ |",
    "Line with \x0b vertical tab and \x07 bell and emoji 😀 kept\n| Header only |\n| --- |",
    "| # | Task | Owner |\n| --- | --- | --- |\n| 1 | Extra | cell | Bob |",
    "Don`t | split\n| A | B |\n|---|---|\n| x | y |",
    "",
]
dump("mdBlocks", {
    "parse": [{"input": m, "blocks": [asdict(b) for b in md_blocks.parse(m)]} for m in MD],
    "runs": [{"input": s, "runs": [[t, b] for t, b in md_blocks.runs(s)]}
             for s in ["plain", "**bold** start", "mid **b** end", "**a****b**", "un**closed"]],
    "clean": [{"input": s, "output": md_blocks.clean_text(s)} for s in ["a\x00b\x0bc\td\ne\x7ff", "ok ￾"]],
})
dump("rtl", [{"input": s, "arabic": rtl.has_arabic(s)} for s in ["hello", "مرحبا", "שלום", "x ي", ""]])

LONG = " ".join(f"Sentence {i} is about the budget, um, and the plan." for i in range(120))
AR = "هذا اجتماع مهم؟ نعم۔ " * 80
dump("cleaning", {
    "clean": [{"input": s, "fillers": f, "output": cleaning.clean_transcript(s, remove_filler_words=f)}
              for s in ["Um, we should, uh, start. Uh the the plan is ready.\nUm\nUm\nok so so what",
                        "Do you know if he, um, said hmm? I mean kind of.", "Hmm. yes. Erm, maybe.",
                        "Line one\r\nLine one\r\n\r\n\r\n\r\nLine two", "We, uh, agreed, umm, to go."]
              for f in (True, False)],
    "chunk": [{"input": t, "max": m, "overlap": o, "chunks": cleaning.chunk_text(t, m, o)}
              for t, m, o in [(LONG, 500, 100), (LONG, 2000, 400), (AR, 300, 60),
                              ("x" * 1200, 500, 100), ("word " * 400, 300, 80), ("short", 6000, 400)]],
})

dump("prompts", {
    "generation": [{"template": t, "style": s, "transcript": tr,
                    "output": prompts.build_generation_prompt(t, s, tr)}
                   for t in [prompts.BASE_TEMPLATE, "No token template", "A [TRANSCRIPT_HERE] B [TRANSCRIPT_HERE]"]
                   for s in ["Formal Minutes", "Unknown style"]
                   for tr in ["Plain text", "Cost $& and $1 and {idx} [TRANSCRIPT_HERE]"]],
    "reduce": prompts.build_reduce_prompt("T [TRANSCRIPT_HERE]", "Short Summary", "notes $&"),
    "merge": prompts.build_merge_notes_prompt("n {first}", 2, 5, 9),
    "chunk": prompts.CHUNK_SUMMARY_PROMPT.format(idx=3, total=7).replace(prompts.TRANSCRIPT_TOKEN, "part $1"),
})

REF = date(2026, 10, 5)
DEADLINES = ["2026-10-12", "20261012", "12/10/2026", "10/25/2026", "12-10-2026", "12.10.2026",
             "Oct 12, 2026", "October 12, 2026", "12 Oct 2026", "12 October 2026", "Oct 12 2026",
             "12 Oct, 2026", "12th October 2026", "1st Nov", "10 Jan", "Feb 29", "5 Oct", "3 Jul",
             "Not specified", "tbd", "next week", "31/02/2026", "  12 oct  2026. ", "2026-10-12T10:00"]
dump("actionItems", {
    "deadlines": [{"input": s, "ref": REF.isoformat(),
                   "output": (d.isoformat() if (d := tasks.parse_deadline(s, REF)) else None)} for s in DEADLINES],
    "status": [{"input": s, "output": tasks.normalize_status(s)}
               for s in ["done", "In-Progress", "WIP", "", "open", "Blocked", "Cancelled"]],
    "priority": [{"input": s, "output": tasks.normalize_priority(s)}
                 for s in ["urgent", "P2", "low", "", "n/a", "Whenever"]],
    "extract": [{"input": m, "items": [asdict(i) for i in tasks.extract_action_items(m)]}
                for m in MD + ["| Action | Owner | Due | State | Priority |\n|---|---|---|---|---|\n"
                               "| **Ship** it | Ana | 1st Nov | done | urgent |\n| ... | x | | | |"]],
    "csvSafe": [{"input": s, "output": tasks.csv_safe(s)} for s in ["=SUM(A1)", "+1", "-x", "@a", "\tt", "ok"]],
})


def item(task, owner, deadline, status, mid, title, mdate, prio=""):
    it = tasks.ActionItem(task=task, owner=owner, deadline=deadline, status=status, meeting_id=mid,
                          meeting_title=title, meeting_date=mdate, priority=prio)
    it.okey = it.key()
    return it


ITEMS = [item("Send report, final", "Ali", "10 Oct", "Pending", 1, "Budget; Q4", "2026-10-05", "High"),
         item("Send report, final", "Ali", "10 Oct", "Pending", 1, "Budget; Q4", "2026-10-05"),
         item("إرسال التقرير النهائي إلى الإدارة العليا قبل نهاية الأسبوع القادم", "علي", "2026-10-20",
              "in progress", 2, "اجتماع", "2026-10-06"),
         item("No date", "", "tbd", "Pending", 3, "", "2026-10-06")]
ics = calendar_ics.build_ics(ITEMS, today=REF)
dump("ics", {"items": [asdict(i) for i in ITEMS],
             "ics": "\r\n".join(ln for ln in ics.split("\r\n") if not ln.startswith("DTSTAMP:")),
             "uids": [calendar_ics.event_uid(i) for i in ITEMS]})

COERCE = [("logo_position", "Centre"), ("logo_position", "middle"), ("page_number_position", "Bottom Right"),
          ("page_number_position", "top_center"), ("pdf_design", "MODERN"), ("pdf_design", "fancy"),
          ("accent_color", "#abc"), ("accent_color", "red;}</style><script>"), ("show_page_numbers", "no"),
          ("show_page_numbers", ""), ("show_page_numbers", True), ("logo_width_mm", "120"),
          ("logo_width_mm", "abc"), ("logo_width_mm", 5), ("phone", 96824123456.0), ("phone", None),
          ("name", "  Acme  "), ("page_number_format", "  "), ("footer_alignment", "RIGHT")]
dump("profiles", {
    "coerce": [{"key": k, "value": v, "output": profiles._coerce(k, v)} for k, v in COERCE],
    "normHeader": [{"input": h, "output": profiles._norm_header(h)}
                   for h in ["Company Name", " Phone ", "Logo-File", "Accent Colour", "design", "unknown col"]],
    "normalize": [{"input": r, "output": profiles._normalize_record(r)}
                  for r in [{"Company": "Acme", "Tel": "1", "junk": 2}, {"name": "", "phone": None}, {"id": "x"}]],
})


def synth_voice(seconds, f0, tract, seed, gain=0.3):
    vowels = [(730, 1090, 2440), (270, 2290, 3010), (300, 870, 2240), (530, 1840, 2480),
              (570, 840, 2410), (440, 1020, 2240), (660, 1720, 2410), (490, 1350, 1690)]
    rng = np.random.default_rng(seed)
    sr, out, total = 16000, [], 0
    while total < seconds * sr:
        n = int(0.16 * sr)
        t = np.arange(n) / sr
        pitch = f0 * (1 + 0.04 * np.sin(2 * np.pi * rng.uniform(2, 5) * t))
        phase = 2 * np.pi * np.cumsum(pitch) / sr
        formants = [f * tract for f in vowels[rng.integers(len(vowels))]]
        k = np.arange(1, int(3800 / f0) + 1)
        amp = sum(1.0 / (1 + ((k * f0 - fc) / (60 + 0.06 * fc)) ** 2) for fc in formants) / k ** 0.5
        v = (amp[:, None] * np.sin(k[:, None] * phase[None, :])).sum(0)
        v *= np.hanning(n) * gain / (np.abs(v).max() + 1e-9)
        out.append(v)
        if rng.random() < 0.25:
            out.append(np.zeros(int(rng.uniform(0.05, 0.2) * sr)))
        total += sum(len(o) for o in out[-2:])
    sig = np.concatenate(out)[: int(seconds * sr)]
    return (sig + rng.normal(0, 0.002, sig.size)).astype("float32")


class Seg:
    def __init__(self, a, b):
        self.start, self.end, self.speaker = a, b, None


def clip(parts):
    audio, segs, t = [], [], 0.0
    for a in parts:
        segs.append((t, t + a.size / 16000))
        audio += [a, np.zeros(4000, "float32")]
        t += a.size / 16000 + 0.25
    pcm = np.clip(np.concatenate(audio) * 32768, -32768, 32767).astype("<i2")
    wav = Path(tempfile.mkdtemp()) / "c.wav"
    import soundfile as sf
    sf.write(str(wav), pcm.astype("float32") / 32768.0, 16000, subtype="PCM_16")
    labels = diarization.diarize(str(wav), [Seg(a, b) for a, b in segs])
    stats_m, stats_n = diarization._voice_stats(pcm.astype("float32") / 32768.0, [Seg(a, b) for a, b in segs])
    return {"pcm16": base64.b64encode(pcm.tobytes()).decode("ascii"), "segments": segs, "labels": labels,
            "M": stats_m.tolist(), "N": stats_n.tolist()}


lo, hi = dict(f0=115.0, tract=1.0), dict(f0=205.0, tract=1.17)
dump("diarization", {
    "two": clip([synth_voice(3.0, seed=i, gain=(0.3, 0.12)[i % 2], **v)
                 for i, v in enumerate([lo, hi, lo, hi, lo, hi])]),
    "one": clip([synth_voice(2.5, seed=40 + i, gain=g, **lo) for i, g in enumerate((0.3, 0.05, 0.2, 0.1))]),
})


class M:
    def __init__(self, mid, title, ts, minutes):
        self.id, self.title, self.created_at, self.updated_at, self.minutes = mid, title, ts, ts, minutes


class H:
    def __init__(self, ms):
        self.ms = ms

    def list_all(self):
        return self.ms


class S:
    def __init__(self, items):
        self.items = items

    def all_items(self):
        return self.items


def noon(y, m, d):
    return datetime(y, m, d, 12, 0).timestamp()


MEETINGS = [M(1, "Budget", noon(2026, 10, 5), MD[0] + "\nbudget budget forecast forecast forecast"),
            M(2, "اجتماع", noon(2026, 9, 28), "# محضر\nالميزانية الميزانية التسويق"),
            M(3, "Ops", noon(2026, 8, 3), "Logistics warehouse logistics")]
INS_ITEMS = [item("A", "Ali", "2026-10-01", "Pending", 1, "Budget", "2026-10-05"),
             item("B", "Ali", "2026-10-30", "In Progress", 1, "Budget", "2026-10-05"),
             item("C", "Sara", "", "Completed", 2, "x", "2026-09-28"),
             item("D", "tbd", "", "Cancelled", 2, "x", "2026-09-28"),
             item("E", "Sara", "1 Oct", "Pending", 3, "Ops", "2026-08-03")]
ins = insights.compute_insights(H(MEETINGS), S(INS_ITEMS), today=date(2026, 10, 15))
dump("insights", {
    "meetings": [{"id": str(m.id), "title": m.title, "createdAt": int(m.created_at * 1000), "minutes": m.minutes}
                 for m in MEETINGS],
    "items": [asdict(i) for i in INS_ITEMS], "today": "2026-10-15",
    "result": {"totalMeetings": ins.total_meetings, "totalItems": ins.total_items, "openItems": ins.open_items,
               "overdueItems": ins.overdue_items, "completionRate": ins.completion_rate,
               "statusCounts": ins.status_counts, "byOwner": [list(x) for x in ins.by_owner],
               "cadence": [list(x) for x in ins.cadence], "keywords": [list(x) for x in ins.keywords]},
})

VERS = [("1.10", "1.9"), ("v1.2.3", "1.2.3"), ("1.3.0", "1.3.0-rc1"), ("1.3.0-rc1", "1.3.0"),
        ("junk", "1.0.0"), ("2", "1.9.9"), ("1.2", "1.2.0"), ("1.0.1", "1.0.0.9")]
H64 = "ab" * 32
dump("updater", {
    "newer": [{"latest": a, "current": b, "output": updater.is_newer(a, b)} for a, b in VERS],
    "checksum": [{"text": t, "file": f, "bare": b, "output": updater.parse_checksum(t, f, allow_bare=b)}
                 for t, f, b in [(f"{H64}  app.apk", "app.apk", False), (f"{H64} *app.apk", "APP.apk", False),
                                 (f"{H64}  other.apk", "app.apk", False), (H64, "app.apk", True),
                                 (H64, "app.apk", False), (f"x {H64} y", "", False), ("", "a", True)]],
})
print(f"fixtures written to {OUT}")
