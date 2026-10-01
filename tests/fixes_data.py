"""Regression tests for the data / business-logic / export fixes in
docs/BUG_REPORT.md.

    python tests/fixes_data.py

  H5   History shows transcript-only meetings as "Draft" (and the filter works)
  M6   Arabic themes, cadence + meeting date from created_at, no 500 cap
  M13  identical task text in one meeting keeps separate overrides (+ migration)
  M14  year-less deadlines, 29 Feb, "Oct 5"
  M15  stable .ics UIDs, folding by octets
  M16  .ics import: unescape, UTC -> local, VALARM ignored
  M17  markdown: escaped pipes, ###, numbered lists, table after text
  M18  HTML: accent colour + logo src can't inject markup
  M19  DOCX: **bold** rendered, control characters don't crash
  M20  deleting / importing profiles never shares or deletes another's logo
  M21  one bad meeting-type entry doesn't wipe the custom types
  M22  atomic writes + unreadable files kept aside (.bad)
  M23  choosing a logo changes nothing until Save
  M24  recipients split on , and ; and are validated
  M30  CSV: UTF-8 BOM + formula-injection guard
  L8   blank xlsx rows skipped; headers case/space-insensitive

Runs against a THROWAWAY data folder (LOCALAPPDATA is redirected before the app
is imported) and needs no network, Ollama, Whisper or audio hardware.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="mico360_data_"))
os.environ["LOCALAPPDATA"] = str(_TMP)
os.environ["XDG_DATA_HOME"] = str(_TMP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONUTF8", "1")
ROOT = Path(os.environ.get("MICO360_TEST_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from PySide6.QtWidgets import QApplication              # noqa: E402

app = QApplication.instance() or QApplication([])

from mico360 import config                              # noqa: E402

assert str(config.DATA_DIR).startswith(str(_TMP)), f"not isolated: {config.DATA_DIR}"
config.ensure_dirs()

results: list[tuple[str, bool, str]] = []
WORK = _TMP / "work"
WORK.mkdir(parents=True, exist_ok=True)


def check(name: str, cond, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))


def _png(path: Path, color: str) -> Path:
    from PySide6.QtGui import QColor, QImage
    img = QImage(16, 16, QImage.Format_RGB32)
    img.fill(QColor(color))
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path), "PNG")
    return path


class _Toast:
    def show_message(self, *a, **k):
        pass


# =============================================================================
def test_history_draft() -> None:
    print("H5 — History shows transcript-only meetings as Draft")
    from types import SimpleNamespace
    from mico360.core.history import History, Meeting
    from mico360.core.tasks import ActionItemStore
    from mico360.ui.history_page import HistoryPage

    h = History(WORK / "h5.db")
    h.save(Meeting(0, "Has minutes", 0, 0, transcript="t", minutes="# M"))
    h.save(Meeting(0, "Transcript only", 0, 0, transcript="hello there", minutes=""))
    h.save(Meeting(0, "Blank", 0, 0, transcript="   \n", minutes=""))
    rows = {m.title: m for m in h.list()}
    check("H5: list() still omits the transcript text",
          rows["Transcript only"].transcript == "")
    check("H5: list() carries has_transcript",
          rows["Transcript only"].has_transcript and not rows["Blank"].has_transcript
          and rows["Has minutes"].has_transcript)
    st = HistoryPage._status_of
    check("H5: statuses are Complete / Draft / Empty",
          (st(rows["Has minutes"]), st(rows["Transcript only"]), st(rows["Blank"]))
          == ("Complete", "Draft", "Empty"))
    ctx = SimpleNamespace(history=h, settings=config.Settings(WORK / "h5_settings.json"),
                          action_items=ActionItemStore(h, status_file=WORK / "h5_status.json"))
    page = HistoryPage(ctx, _Toast(), lambda m: None)
    page.status_filter.setCurrentText("Draft")
    page.reload()
    titles = [page.table.item(r, 0).text() for r in range(page.table.rowCount())]
    check("H5: Draft filter matches the transcript-only meeting",
          titles == ["Transcript only"], str(titles))
    status_txt = page.table.item(0, 1).text() if titles else ""
    check("H5: table status column says Draft", "Draft" in status_txt, status_txt)
    page.status_filter.setCurrentText("Empty")
    page.reload()
    titles = [page.table.item(r, 0).text() for r in range(page.table.rowCount())]
    check("H5: Empty filter matches only the blank meeting", titles == ["Blank"], str(titles))
    page.deleteLater()
    h.close()


# =============================================================================
def test_insights() -> None:
    print("M6 — insights: Arabic themes, created_at cadence/meeting date, no 500 cap")
    from datetime import date, timedelta
    from mico360.core.history import History, Meeting
    from mico360.core.insights import compute_insights
    from mico360.core.tasks import ActionItemStore

    h = History(WORK / "m6.db")
    ar = ("# محضر الاجتماع\n## القرارات\n- اعتماد الميزانية للمشروع\n"
          "- مراجعة الميزانية مع الموردين\n- الميزانية النهائية للمشروع")
    h.save(Meeting(0, "Arabic", 0, 0, minutes=ar))
    h.save(Meeting(0, "Arabic 2", 0, 0, minutes="مناقشة الميزانية والمشروع الجديد"))
    ins = compute_insights(h, ActionItemStore(h, status_file=WORK / "m6_a.json"))
    words = [w for w, _ in ins.keywords]
    check("M6: Arabic minutes produce recurring themes",
          "الميزانية" in words, str(words))
    check("M6: Arabic stop/boilerplate words are not themes",
          "الاجتماع" not in words and "محضر" not in words, str(words))
    h.close()

    # cadence + meeting date use created_at, not updated_at
    h = History(WORK / "m6b.db")
    mid = h.save(Meeting(0, "Old meeting", 0, 0,
                         minutes="| Task | Owner | Deadline |\n| - | - | - |\n| Ship | Bob | TBD |"))
    old_ts = time.time() - 40 * 86400
    h._conn.execute("UPDATE meetings SET created_at=? WHERE id=?", (old_ts, mid))
    h._conn.commit()
    m = h.get(mid); m.title = "Old meeting (edited today)"; h.save(m)   # bumps updated_at
    store = ActionItemStore(h, status_file=WORK / "m6_b.json")
    ins = compute_insights(h, store, weeks=4)
    this_week = ins.cadence[-1][1]
    check("M6: editing an old meeting doesn't move it into this week's cadence",
          this_week == 0, str(ins.cadence))
    it = store.all_items()[0]
    want = time.strftime("%Y-%m-%d", time.localtime(old_ts))
    check("M6: action item 'Meeting date' is when the meeting was held",
          it.meeting_date == want, f"{it.meeting_date} != {want}")
    h.close()

    # more than 500 meetings are all aggregated
    h = History(WORK / "m6c.db")
    for k in range(520):
        h._conn.execute(
            "INSERT INTO meetings (title, created_at, updated_at, minutes) VALUES (?,?,?,?)",
            (f"m{k}", k, k, f"| Task | Owner |\n| - | - |\n| Task {k} | Sam |"))
    h._conn.commit()
    store = ActionItemStore(h, status_file=WORK / "m6_c.json")
    ins = compute_insights(h, store)
    check("M6: insights count every meeting (520, not 500)",
          ins.total_meetings == 520, str(ins.total_meetings))
    check("M6: action items aggregated from every meeting",
          len(store.all_items()) == 520, str(len(store.all_items())))
    check("M6: History list view still capped at 500 by default", len(h.list()) == 500)
    h.close()
    _ = date, timedelta


# =============================================================================
def test_task_overrides() -> None:
    print("M13 — identical task text keeps separate overrides; legacy keys migrate")
    import hashlib
    import json
    from mico360.core.history import History, Meeting
    from mico360.core.tasks import ActionItemStore

    h = History(WORK / "m13.db")
    md = ("## Action Items\n| Task | Responsible | Deadline | Status |\n| - | - | - | - |\n"
          "| Send report | Alice | 2030-01-01 | Pending |\n"
          "| Send report | Bob | 2030-02-02 | Pending |\n"
          "| Book room | Carol | 2030-03-03 | Pending |")
    mid = h.save(Meeting(0, "Dupes", 0, 0, minutes=md))
    sf = WORK / "m13_status.json"
    store = ActionItemStore(h, status_file=sf)
    items = store.all_items()
    alice = next(i for i in items if i.owner == "Alice")
    store.set_status(alice, "Completed")
    items = store.all_items()
    by_owner = {i.owner: i for i in items}
    check("M13: completing Alice's row doesn't complete Bob's",
          by_owner["Alice"].status == "Completed" and by_owner["Bob"].status == "Pending",
          str({k: v.status for k, v in by_owner.items()}))
    store.update_item(by_owner["Bob"], task="Send report", owner="Bob", deadline="2031-05-05",
                      priority="High", status="In Progress", notes="")
    by_owner = {i.owner: i for i in store.all_items()}
    check("M13: editing Bob's row doesn't copy onto Alice's",
          by_owner["Alice"].deadline == "2030-01-01" and by_owner["Bob"].deadline == "2031-05-05")
    check("M13: row keys are unique", len({i.okey for i in store.all_items()}) == 3)

    # --- migration of the old (meeting + md5(text)) keys --------------------
    def old(text):
        return f"{mid}:" + hashlib.md5(text.strip().lower().encode("utf-8")).hexdigest()[:10]
    sf2 = WORK / "m13_legacy.json"
    sf2.write_text(json.dumps({
        old("Book room"): {"status": "Completed", "notes": "done by phone", "owner": "Carol",
                           "task": "Book room", "deadline": "2030-03-03", "priority": "Low"},
        old("Send report"): "In Progress",            # legacy status-only, shared by 2 rows
    }), encoding="utf-8")
    st2 = ActionItemStore(h, status_file=sf2)
    items = st2.all_items()
    book = next(i for i in items if i.task == "Book room")
    check("M13: unambiguous legacy override migrated (edits kept)",
          book.status == "Completed" and book.notes == "done by phone" and book.priority == "Low")
    sends = [i for i in items if i.task == "Send report"]
    check("M13: shared legacy override kept on every row it applied to",
          all(i.status == "In Progress" for i in sends) and len(sends) == 2)
    saved = json.loads(sf2.read_text(encoding="utf-8"))
    check("M13: legacy keys rewritten to per-row keys",
          old("Book room") not in saved and old("Send report") not in saved
          and book.okey in saved and all(i.okey in saved for i in sends), str(list(saved)))
    st2.set_status(sends[0], "Completed")
    again = [i for i in st2.all_items() if i.task == "Send report"]
    check("M13: after migration the two rows are independent",
          sorted(i.status for i in again) == ["Completed", "In Progress"])
    h.close()


# =============================================================================
def test_deadlines() -> None:
    print("M14 — year-less deadlines")
    from datetime import date
    from mico360.core import tasks as T
    from mico360.core.tasks import ActionItem

    check("M14: '10 Jan' written on 20 Dec means next January",
          T.parse_deadline("10 Jan", ref=date(2025, 12, 20)) == date(2026, 1, 10))
    it = ActionItem("Pay", "Sam", "10 Jan", "Pending", meeting_date="2025-12-20")
    check("M14: ...so it isn't overdue the next day",
          not T.is_overdue(it, today=date(2025, 12, 21)))
    check("M14: '29 Feb' parses (next leap year)",
          T.parse_deadline("29 Feb", ref=date(2027, 1, 5)) == date(2028, 2, 29))
    check("M14: '29 Feb' in a leap year", T.parse_deadline("29 Feb", ref=date(2028, 1, 5))
          == date(2028, 2, 29))
    check("M14: 'Oct 5' parses", T.parse_deadline("Oct 5", ref=date(2026, 9, 1))
          == date(2026, 10, 5))
    check("M14: 'October 5th' parses", T.parse_deadline("October 5th", ref=date(2026, 9, 1))
          == date(2026, 10, 5))
    check("M14: a recent past year-less date stays this year (overdue)",
          T.parse_deadline("1 Mar", ref=date(2026, 3, 20)) == date(2026, 3, 1))
    check("M14: explicit years unchanged",
          T.parse_deadline("2020-01-15") == date(2020, 1, 15)
          and T.parse_deadline("15/01/2020") == date(2020, 1, 15)
          and T.parse_deadline("Oct 5, 2027") == date(2027, 10, 5))


# =============================================================================
def test_ics_export() -> None:
    print("M15 — .ics: stable UIDs, octet folding, re-import")
    import re
    from mico360.core import calendar_ics
    from mico360.core.calendar_import import parse_ics
    from mico360.core.tasks import ActionItem

    def items():
        a = ActionItem("Ship v2, phase 1; final", "Bob", "2030-01-10", "Pending", 7, "Kickoff")
        a.okey = "7:abc:0"
        b = ActionItem("مراجعة الميزانية السنوية مع فريق المالية والموردين قبل نهاية الربع الحالي",
                       "أحمد", "2030-01-12", "Pending", 7, "اجتماع الميزانية")
        b.okey = "7:def:0"
        return [a, b]

    ics1 = calendar_ics.build_ics(items())
    time.sleep(1.1)
    ics2 = calendar_ics.build_ics(items())
    uid1 = re.findall(r"^UID:(.*)$", ics1, re.M)
    uid2 = re.findall(r"^UID:(.*)$", ics2, re.M)
    check("M15: UIDs are stable across exports", uid1 and uid1 == uid2, f"{uid1} vs {uid2}")
    check("M15: UIDs differ per item", len(set(uid1)) == 2)
    lines = ics1.encode("utf-8").split(b"\r\n")
    longest = max(len(ln) for ln in lines)
    check("M15: every line <= 75 octets (Arabic included)", longest <= 75, str(longest))
    check("M15: CRLF only (no bare LF / CRCRLF)",
          b"\r\r\n" not in ics1.encode() and ics1.count("\n") == ics1.count("\r\n"))
    p = WORK / "m15.ics"
    calendar_ics.write_ics(p, [items()[0]])
    d = parse_ics(p)
    check("M15/M16: re-importing our own .ics keeps the full title",
          d["title"] == "[Bob] Ship v2, phase 1; final", repr(d["title"]))
    calendar_ics.write_ics(p, [items()[1]])
    d = parse_ics(p)
    check("M15: re-imported Arabic title survives octet folding",
          d["title"] == "[أحمد] " + items()[1].task, repr(d["title"]))


def test_ics_import() -> None:
    print("M16 — .ics import: unescape, UTC -> local, VALARM ignored")
    from datetime import datetime, timezone
    from mico360.core.calendar_import import parse_ics
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
           "SUMMARY:Budget\\, Q3\\; final review\r\n"
           "DTSTART:20250714T100000Z\r\n"
           'ORGANIZER;CN="Alice Boss":mailto:alice@x.com\r\n'
           "ATTENDEE;CN=John Doe:mailto:john@x.com\r\n"
           "BEGIN:VALARM\r\nACTION:EMAIL\r\nSUMMARY:Reminder\r\n"
           "DESCRIPTION:Reminder\r\nATTENDEE:mailto:alarm-bot@x.com\r\n"
           "TRIGGER:-PT15M\r\nEND:VALARM\r\n"
           "END:VEVENT\r\nEND:VCALENDAR\r\n")
    p = WORK / "m16.ics"
    p.write_bytes(ics.encode("utf-8"))
    d = parse_ics(p)
    check("M16: \\, and \\; unescaped", d["title"] == "Budget, Q3; final review", repr(d["title"]))
    want = datetime(2025, 7, 14, 10, 0, tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    check("M16: UTC (Z) time converted to local time", d["date"] == want, f"{d['date']} != {want}")
    check("M16: VALARM SUMMARY doesn't replace the title", "Reminder" not in d["title"])
    check("M16: VALARM attendee (alarm bot) not listed",
          d["attendees"] == ["Alice Boss", "John Doe"], str(d["attendees"]))
    # VALARM before SUMMARY: title still the event's
    ics2 = ("BEGIN:VEVENT\nBEGIN:VALARM\nSUMMARY:Reminder\nEND:VALARM\n"
            "SUMMARY:Real title\nDTSTART;VALUE=DATE:20251225\nEND:VEVENT")
    p.write_text(ics2, encoding="utf-8")
    d = parse_ics(p)
    check("M16: event after a VALARM still parsed",
          d["title"] == "Real title" and d["date"] == "2025-12-25", str(d))


# =============================================================================
TRICKY_MD = (
    "# Weekly **Sync** Minutes\n"
    "**Meeting Title:** Sprint **planning**\x0b review\n"
    "Intro paragraph line.\n"
    "Task | Responsible | Deadline | Status\n"
    "--- | --- | --- | ---\n"
    "Compare A\\|B pricing | Sam | 2030-01-01 | Pending\n"
    "\n"
    "## Discussion\n"
    "### Budget details\n"
    "Budget body text.\n"
    "1. First point\n"
    "2. Second **bold** point\n"
    "3) Third point\n"
    "\n"
    "#### Deep heading\n"
    "| # | Task | Responsible | Deadline |\n"
    "| - | - | - | - |\n"
    "| 1 | Use `a|b` flag | Lee | 2030-02-02 |\n"
    "| 2 | Review x | y | z | Kim | 2030-03-03 |\n"
    "| 3 | **Bold task**\x01 | Ann | 2030-04-04 |\n"
)


def test_md_blocks() -> None:
    print("M17 — markdown parser: pipes in cells, ###, numbered lists, table after text")
    from mico360.export import md_blocks
    from mico360.core.tasks import extract_action_items
    blocks = md_blocks.parse(TRICKY_MD)
    kinds = [b.kind for b in blocks]
    tables = [b for b in blocks if b.kind == "table"]
    check("M17: table directly after a paragraph line is parsed",
          len(tables) == 2 and kinds[:4] == ["h1", "kv", "para", "table"], str(kinds))
    t1 = tables[0] if tables else None
    check("M17: escaped \\| stays inside the cell (no column shift)",
          t1 is not None and t1.rows[0] == ["Compare A|B pricing", "Sam", "2030-01-01", "Pending"],
          str(t1.rows if t1 else None))
    h3 = [b for b in blocks if b.kind == "h3"]
    check("M17: ### and #### are headings (not merged into the body)",
          [(b.text, b.level) for b in h3] == [("Budget details", 3), ("Deep heading", 4)]
          and any(b.kind == "para" and b.text == "Budget body text." for b in blocks),
          str([(b.kind, b.text) for b in blocks]))
    ol = [b for b in blocks if b.kind == "olist"]
    check("M17: numbered list is an ordered-list block with 3 items",
          len(ol) == 1 and len(ol[0].items) == 3 and ol[0].start == 1
          and ol[0].items[1] == "Second **bold** point", str(ol[0].__dict__ if ol else None))
    t2 = tables[1] if len(tables) > 1 else None
    check("M17: pipe inside `code` doesn't split the cell",
          t2 is not None and t2.rows[0][1] == "Use `a|b` flag" and t2.rows[0][2] == "Lee",
          str(t2.rows if t2 else None))
    check("M17: unescaped pipe in a cell merged back, owner/deadline keep their columns",
          t2 is not None and t2.rows[1][2:] == ["Kim", "2030-03-03"]
          and t2.rows[1][1] == "Review x | y | z", str(t2.rows[1] if t2 else None))
    check("M17: control characters removed", "\x0b" not in blocks[1].text
          and (t2 is not None and "\x01" not in t2.rows[2][1]))
    items = extract_action_items(TRICKY_MD)
    by_task = {i.task: i for i in items}
    check("M17: action items get the right owner + deadline",
          by_task.get("Compare A|B pricing") is not None
          and by_task["Compare A|B pricing"].owner == "Sam"
          and by_task["Compare A|B pricing"].deadline == "2030-01-01"
          and by_task.get("Review x | y | z") is not None
          and by_task["Review x | y | z"].owner == "Kim"
          and by_task["Review x | y | z"].deadline == "2030-03-03",
          str([(i.task, i.owner, i.deadline) for i in items]))


def test_exports() -> None:
    print("M17/M18/M19 — all 5 export formats from tricky minutes + a hostile profile")
    import zipfile
    from mico360.core.profiles import CompanyProfile
    from mico360.export import service
    from mico360.export.html_export import render_document, render_fragment

    logo_dir = WORK / "it's #1 logos"
    logo = _png(logo_dir / "logo #1.png", "#3366cc")
    evil = "#fff'><script>alert(1)</script>"
    prof = CompanyProfile(name="Evil <b>Co</b>", accent_color=evil, logo_path=str(logo),
                          footer_text="foot\x0bnote", phone="123")
    ok = True
    sizes = {}
    for ext in (".txt", ".md", ".html", ".docx", ".pdf"):
        try:
            p = service.export(TRICKY_MD, str(WORK / f"tricky{ext}"), prof)
            sizes[ext] = Path(p).stat().st_size
            ok = ok and sizes[ext] > 100
        except Exception as exc:
            ok = False
            sizes[ext] = repr(exc)
    check("M17-M19: all 5 formats export", ok, str(sizes))

    html = render_document(TRICKY_MD, prof)
    check("M18: malicious accent colour can't inject markup",
          "<script" not in html and "alert(1)" not in html and "#8B1E1E" in html)
    check("M18: profile name escaped", "Evil &lt;b&gt;Co&lt;/b&gt;" in html)
    uri = logo.resolve().as_uri()
    check("M18: logo src is a proper file URI (quote and # encoded)",
          f'src="{uri}"' in html and "%23" in uri and "%27" in uri, uri)
    frag = render_fragment(TRICKY_MD, accent="red;background:url(x)")
    check("M18: invalid accent in preview falls back", "url(x)" not in frag)
    check("M17: HTML renders h3/h4 and the ordered list",
          "<h3" in html and "<h4" in html and "<ol>" in html and html.count("<li") >= 3)

    dx = zipfile.ZipFile(WORK / "tricky.docx").read("word/document.xml").decode("utf-8")
    hdr = "".join(zipfile.ZipFile(WORK / "tricky.docx").read(n).decode("utf-8")
                  for n in zipfile.ZipFile(WORK / "tricky.docx").namelist()
                  if n.startswith("word/header") or n.startswith("word/footer"))
    check("M19: DOCX has no literal ** in headings, key/value lines or cells",
          "**" not in dx, dx[dx.find("**") - 60: dx.find("**") + 60] if "**" in dx else "")
    check("M19: DOCX renders bold spans (heading, kv value, cell)",
          "<w:b/>" in dx and "Sync" in dx and "planning" in dx and "Bold task" in dx)
    check("M19: DOCX survives control characters (\\x0b, \\x01)",
          "\x0b" not in dx and "\x01" not in dx and "footnote" in hdr)
    import re as _re
    check("M17: DOCX renders the numbered list (3.) and ###/#### as Heading 2/3",
          "First point" in dx and _re.search(r">3\.(</w:t>|\s)", dx) is not None
          and 'w:val="Heading2"' in dx and 'w:val="Heading3"' in dx)
    check("M17: DOCX escaped pipe text intact", "Compare A|B pricing" in dx)
    try:
        from pypdf import PdfReader
        txt = "".join((pg.extract_text() or "") for pg in PdfReader(WORK / "tricky.pdf").pages)
        check("M17: PDF contains the numbered items and h3 heading",
              "First point" in txt and "Budget details" in txt and "3." in txt)
    except ImportError:
        check("M17: PDF built (pypdf not installed for text check)",
              isinstance(sizes.get(".pdf"), int))
    txt = (WORK / "tricky.txt").read_text(encoding="utf-8")
    check("M19: TXT strips control characters", "\x0b" not in txt and "\x01" not in txt)


def test_arabic_pdf_still_ok() -> None:
    print("Arabic exports still work (H20-H22 unaffected)")
    from mico360.export import service
    md = ("# محضر الاجتماع\n**التاريخ:** ٢٢ سبتمبر\n### تفاصيل الميزانية\n"
          "1. الموافقة على الميزانية\n2. مراجعة العقود\n\n"
          "| المهمة | المسؤول |\n| - | - |\n| تقرير \\| ملخص | أحمد |")
    ok = True
    for ext in (".pdf", ".docx", ".html"):
        try:
            p = service.export(md, str(WORK / f"ar{ext}"))
            ok = ok and Path(p).stat().st_size > 100
        except Exception as exc:
            ok = False
            print("   ", ext, exc)
    check("Arabic minutes with ### / numbered list / escaped pipe export to PDF/DOCX/HTML", ok)


# =============================================================================
def test_profiles_logos() -> None:
    print("M20/M23/L8 — profile logos and import")
    import json
    from mico360.core.profiles import CompanyProfile, ProfileStore

    st = ProfileStore(WORK / "profiles")
    a = st.save(CompanyProfile(name="Alpha"))
    a = st.set_logo(a, _png(WORK / "src" / "alpha.png", "#ff0000"))
    a = st.save(a)
    a_logo = Path(a.logo_path)
    exp = WORK / "alpha_export.json"
    st.export_file([a], exp)
    imp = st.import_file(exp)[0]
    check("M20: imported copy gets its OWN logo file",
          imp.logo_path and Path(imp.logo_path).exists() and Path(imp.logo_path) != a_logo)
    st.delete(imp.id)
    check("M20: deleting the imported copy keeps the original's logo", a_logo.exists())
    # a legacy store where two profiles already share one file
    b = st.save(CompanyProfile(name="Beta", logo_path=str(a_logo)))
    st.delete(b.id)
    check("M20: deleting a profile never removes a logo another profile uses", a_logo.exists())

    # M23 — dialog: choose logo then Cancel changes nothing; Save swaps + cleans up
    from PySide6.QtWidgets import QFileDialog
    from mico360.ui import dialogs as D
    new_src = _png(WORK / "src" / "new logo.png", "#00ff00")
    before = a_logo.read_bytes()
    orig = QFileDialog.getOpenFileName
    QFileDialog.getOpenFileName = staticmethod(lambda *a_, **k: (str(new_src), ""))
    try:
        dlg = D.ProfileDialog(st, st.get(a.id))
        dlg._choose_logo()
        dlg.reject()
        saved = st.get(a.id)
        check("M23: Cancel after choosing a logo leaves the saved profile unchanged",
              saved.logo_path == str(a_logo) and a_logo.read_bytes() == before)
        check("M23: nothing copied into the store before Save",
              sorted(p.name for p in st.logos.iterdir()) == [a_logo.name],
              str([p.name for p in st.logos.iterdir()]))
        dlg = D.ProfileDialog(st, st.get(a.id))
        dlg._choose_logo()
        dlg._save()
        saved = st.get(a.id)
        check("M23: Save installs the new logo",
              saved.logo_path != str(a_logo) and Path(saved.logo_path).exists()
              and Path(saved.logo_path).read_bytes() == new_src.read_bytes())
        check("M23: the replaced logo file is removed after Save", not a_logo.exists())
    finally:
        QFileDialog.getOpenFileName = orig

    # L8 — xlsx with blank / formatted-empty rows and loose headers
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook(); ws = wb.active
    ws.append(["Company Name", " PHONE ", "Accent Colour", "Footer"])
    ws.append(["Gamma LLC", 96891234567, "#123456", "g"])
    ws.append([None, None, None, None])
    ws.append(["", "  ", None, ""])
    for c in ("A5", "B5", "C5", "D5"):
        ws[c].font = Font(bold=True)             # formatted but empty row
    ws.append(["Delta", None, "red'><x", None])
    xp = WORK / "l8.xlsx"; wb.save(xp)
    got = st.import_file(xp)
    names = [p.name for p in got]
    check("L8: blank / formatted-empty rows skipped", names == ["Gamma LLC", "Delta"], str(names))
    check("L8: headers matched case/space-insensitively",
          got and got[0].phone == "96891234567" and got[0].accent_color == "#123456"
          and got[0].footer_text == "g")
    check("M18: imported invalid accent colour replaced",
          len(got) > 1 and got[1].accent_color == "#8B1E1E")
    _ = json


# =============================================================================
def test_meeting_types() -> None:
    print("M21/M22 — meeting types survive a bad entry / unreadable file")
    import json
    from mico360.core.meeting_types import MeetingTypeStore, MeetingType
    f = WORK / "mt.json"
    f.write_text(json.dumps([{"name": "My Custom", "prompt_name": "P", "style": "S"},
                             5, {"prompt_name": "no name"}, {"name": "Second", "style": 7}]),
                 encoding="utf-8")
    st = MeetingTypeStore(f)
    names = [t.name for t in st.list()]
    check("M21: one bad entry doesn't wipe the custom types",
          names == ["My Custom", "Second"], str(names))
    check("M21: the original file is kept as .bad", (WORK / "mt.json.bad").exists())
    st.save(MeetingType("Third"))
    check("M21: saving keeps the custom types",
          [d["name"] for d in json.loads(f.read_text(encoding="utf-8"))]
          == ["My Custom", "Second", "Third"])
    g = WORK / "mt2.json"
    g.write_text('[{"name": "Half-writ', encoding="utf-8")
    MeetingTypeStore(g)
    check("M22: unreadable meeting types kept aside, not overwritten",
          (WORK / "mt2.json.bad").read_text(encoding="utf-8") == '[{"name": "Half-writ')


def test_atomic_settings() -> None:
    print("M22 — settings / action status: atomic writes, bad files kept")
    import json
    from mico360.core.history import History
    from mico360.core.tasks import ActionItemStore
    sp = WORK / "settings.json"
    sp.write_text('{"smtp_password": "secret", "smtp_user": "u"', encoding="utf-8")  # truncated
    s = config.Settings(sp)
    s.set("theme", "dark")
    bad = WORK / "settings.json.bad"
    check("M22: unreadable settings.json kept as .bad (credentials not lost)",
          bad.exists() and "secret" in bad.read_text(encoding="utf-8"))
    check("M22: new settings saved and readable",
          json.loads(sp.read_text(encoding="utf-8")).get("theme") == "dark")
    check("M22: no temp files left behind",
          not [p for p in WORK.iterdir() if p.name.endswith(".tmp")])
    # atomic: a failure mid-write leaves the old file intact
    orig_replace = os.replace
    def boom(*a, **k):
        raise OSError("simulated crash")
    os.replace = boom
    try:
        try:
            config.atomic_write_text(sp, "{broken")
        except OSError:
            pass
    finally:
        os.replace = orig_replace
    check("M22: interrupted write leaves the previous file intact",
          json.loads(sp.read_text(encoding="utf-8")).get("theme") == "dark")
    af = WORK / "action_status.json"
    af.write_text('{"1:abc:0": {"status": "Comp', encoding="utf-8")
    h = History(WORK / "m22.db")
    ActionItemStore(h, status_file=af)
    check("M22: unreadable action_status.json kept as .bad",
          (WORK / "action_status.json.bad").exists())
    h.close()


# =============================================================================
def test_recipients() -> None:
    print("M24 — recipients: , and ; with validation")
    from mico360.ui import dialogs as D
    v, bad = D.parse_recipients("a@x.com; b@y.com")
    check("M24: semicolon-separated recipients all kept", v == ["a@x.com", "b@y.com"] and not bad)
    v, bad = D.parse_recipients('"Doe, John" <john@x.com>, c@z.org;; ')
    check("M24: 'Name <addr>' and quoted commas handled", v == ["john@x.com", "c@z.org"]
          and not bad, f"{v} {bad}")
    v, bad = D.parse_recipients("a@x.com, not-an-email, b@y")
    check("M24: invalid addresses reported", v == ["a@x.com"] and bad == ["not-an-email", "b@y"],
          f"{v} {bad}")
    dlg = D.EmailComposeDialog("S", "B", "a@x.com; b@y.com")
    check("M24: dialog sends to every recipient", dlg.values()["to"] == ["a@x.com", "b@y.com"])
    warned = []
    orig = D.QMessageBox.warning
    D.QMessageBox.warning = staticmethod(lambda *a, **k: warned.append(a[2] if len(a) > 2 else ""))
    try:
        dlg2 = D.EmailComposeDialog("S", "B", "a@x.com; oops")
        dlg2._validate()
        check("M24: invalid address blocks Send with a clear message",
              dlg2.result() == 0 and warned and "oops" in warned[0])
    finally:
        D.QMessageBox.warning = orig


def test_csv() -> None:
    print("M30 — CSV: BOM + formula injection")
    import csv
    from mico360.core.history import History
    from mico360.core.tasks import ActionItem, ActionItemStore
    h = History(WORK / "m30.db")
    st = ActionItemStore(h, status_file=WORK / "m30.json")
    items = [ActionItem("=HYPERLINK(\"http://x\",\"y\")", "أحمد", "+1", "Pending",
                        meeting_title="@SUM(A1)", notes="-2+3"),
             ActionItem("Normal task", "Sam", "2030-01-01", "Pending")]
    p = WORK / "items.csv"
    st.export_csv(p, items)
    raw = p.read_bytes()
    check("M30: CSV starts with a UTF-8 BOM (Excel reads Arabic)", raw.startswith(b"\xef\xbb\xbf"))
    rows = list(csv.reader(p.open(encoding="utf-8-sig", newline="")))
    r = rows[1]
    check("M30: Arabic intact", r[1] == "أحمد")
    check("M30: = + - @ cells neutralised",
          r[0].startswith("'=") and r[2] == "'+1" and r[5] == "'-2+3" and r[6] == "'@SUM(A1)",
          str(r))
    check("M30: normal cells untouched", rows[2][0] == "Normal task")
    h.close()


# =============================================================================
def main() -> int:
    for fn in (test_history_draft, test_insights, test_task_overrides, test_deadlines,
               test_ics_export, test_ics_import, test_md_blocks, test_exports,
               test_arabic_pdf_still_ok, test_profiles_logos, test_meeting_types,
               test_atomic_settings, test_recipients, test_csv):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} ran without errors", False, repr(exc))
    passed = sum(ok for _, ok, _ in results)
    print(f"\n==== DATA: {passed}/{len(results)} passed ====")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        shutil.rmtree(_TMP, ignore_errors=True)
        from mico360.hard_exit import hard_exit
        hard_exit(rc)
