"""Adversarial unit tests for the pure-logic parsers that silently drive what
users see in Action Items and exports:

  * export.md_blocks.parse            (Markdown blocks / tables)
  * core.tasks.extract_action_items   (Action-Items table detection)
  * core.calendar_import.parse_ics     (.ics reader)

Focus: malformed tables, missing/weird columns, empty input, and hang safety.
Run:  python tests/test_parsers.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

results: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def _parse_no_hang(md, seconds=5):
    """Run md_blocks.parse in a watchdog thread so an infinite loop fails the
    test instead of freezing the whole run."""
    from mico360.export import md_blocks
    box = {}
    err = {}

    def run():
        try:
            box["b"] = md_blocks.parse(md)
        except Exception as exc:                      # noqa: BLE001
            err["e"] = exc

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(seconds)
    if th.is_alive():
        raise TimeoutError("parse() did not return within %ss — infinite loop" % seconds)
    if "e" in err:
        raise err["e"]
    return box["b"]


def _tables(blocks):
    return [b for b in blocks if b.kind == "table"]


def _write_ics(text: str) -> str:
    fd, p = tempfile.mkstemp(suffix=".ics")
    os.close(fd)
    Path(p).write_text(text, encoding="utf-8")
    return p


# ===========================================================================
def test_md_blocks():
    # --- empty / whitespace ------------------------------------------------
    check("md: empty string → no blocks, no hang", _parse_no_hang("") == [])
    check("md: whitespace only → no blocks", _parse_no_hang("   \n\n\t\n") == [])

    # --- HANG REGRESSION: pipe lines that are NOT valid tables -------------
    check("md: '|' header with no separator does not hang",
          isinstance(_parse_no_hang("| Task | Owner |\n\nnext paragraph"), list))
    check("md: '|' header at end-of-file does not hang",
          isinstance(_parse_no_hang("Intro line\n| Task | Owner |"), list))
    check("md: stray '|' line mid-prose does not hang",
          isinstance(_parse_no_hang("Hello\n| lonely pipe\nWorld"), list))
    check("md: only a separator-looking line does not hang",
          isinstance(_parse_no_hang("---|---"), list))

    # --- valid tables (leading pipe) --------------------------------------
    b = _parse_no_hang("| A | B |\n| - | - |\n| 1 | 2 |\n| 3 | 4 |")
    t = _tables(b)
    check("md: leading-pipe table parsed",
          len(t) == 1 and t[0].headers == ["A", "B"] and t[0].rows == [["1", "2"], ["3", "4"]],
          str(t[0].__dict__) if t else "no table")

    # --- valid table WITHOUT leading pipes (small-model output) -----------
    b = _parse_no_hang("Task | Owner\n--- | ---\nDo X | Me\nDo Y | You")
    t = _tables(b)
    check("md: no-leading-pipe table parsed",
          len(t) == 1 and t[0].headers == ["Task", "Owner"]
          and t[0].rows == [["Do X", "Me"], ["Do Y", "You"]],
          str(t[0].__dict__) if t else "no table")

    # --- alignment separator ----------------------------------------------
    b = _parse_no_hang("| A | B |\n|:--:|:--|\n| x | y |")
    check("md: alignment separator recognised", len(_tables(b)) == 1)

    # --- a heading that contains '|' must stay a heading, not a table ------
    b = _parse_no_hang("## Costs | 2025\n----------\nbody")
    check("md: heading containing '|' is not swallowed as a table",
          any(x.kind == "h2" for x in b) and not _tables(b),
          str([x.kind for x in b]))

    # --- table with ragged rows (fewer/more cells) does not crash ----------
    b = _parse_no_hang("| A | B | C |\n| - | - | - |\n| only-one |\n| a | b | c | d |")
    t = _tables(b)
    check("md: ragged table rows kept without crashing",
          len(t) == 1 and len(t[0].rows) == 2, str(t[0].rows) if t else "no table")

    # --- non-table constructs still work -----------------------------------
    b = _parse_no_hang("# Title\n**Owner:** Sam\n- one\n- two\nA paragraph.")
    kinds = [x.kind for x in b]
    check("md: headings/kv/bullets/paragraph unaffected",
          kinds == ["h1", "kv", "bullet", "para"], str(kinds))


# ===========================================================================
def test_action_items():
    from mico360.core.tasks import extract_action_items

    check("ai: empty minutes → no items", extract_action_items("") == [])
    check("ai: None-safe", extract_action_items(None) == [])
    check("ai: prose with no table → no items",
          extract_action_items("# Minutes\n\nWe talked about things.") == [])

    # a table that is NOT an action-items table must be ignored
    non = "| Name | Role |\n| - | - |\n| Sam | PM |"
    check("ai: non-action table ignored", extract_action_items(non) == [])

    # canonical table
    md = ("## Action Items\n| Task | Responsible Person | Deadline | Status |\n"
          "| - | - | - | - |\n| Ship v2 | Sam | Fri | In Progress |")
    items = extract_action_items(md)
    check("ai: canonical table extracted",
          len(items) == 1 and items[0].task == "Ship v2" and items[0].owner == "Sam"
          and items[0].deadline == "Fri" and items[0].status == "In Progress",
          str(items[0].__dict__) if items else "none")

    # weird header wording + reordered columns + 'state' instead of 'status'
    md = ("| Owner | Action Item | State | By when |\n| - | - | - | - |\n"
          "| Sam | Draft spec | Open | Mon |")
    items = extract_action_items(md)
    check("ai: weird/reordered headers detected",
          len(items) == 1 and items[0].task == "Draft spec" and items[0].owner == "Sam"
          and items[0].status == "Open" and items[0].deadline == "Mon",
          str(items[0].__dict__) if items else "none")

    # missing Status column → defaults to Pending
    md = "| Task | Owner |\n| - | - |\n| Do it | Me |"
    items = extract_action_items(md)
    check("ai: missing status column defaults to Pending",
          len(items) == 1 and items[0].status == "Pending",
          str(items[0].__dict__) if items else "none")

    # placeholder / empty task rows are dropped
    md = ("| Task | Status |\n| - | - |\n| | Done |\n| - | Done |\n"
          "| N/A | Done |\n| TBD | Done |\n| Real task | Done |")
    items = extract_action_items(md)
    check("ai: placeholder rows filtered",
          len(items) == 1 and items[0].task == "Real task",
          [i.task for i in items])

    # bold markers inside cells are stripped
    md = "| Task | Status |\n| - | - |\n| **Bold task** | **Done** |"
    items = extract_action_items(md)
    check("ai: bold markers stripped from cells",
          len(items) == 1 and items[0].task == "Bold task" and items[0].status == "Done",
          str(items[0].__dict__) if items else "none")

    # ragged row (fewer cells than headers) → no IndexError, missing fields blank
    md = "| Task | Owner | Deadline | Status |\n| - | - | - | - |\n| Lonely |"
    items = extract_action_items(md)
    check("ai: ragged row handled without crashing",
          len(items) == 1 and items[0].task == "Lonely" and items[0].owner == "",
          str(items[0].__dict__) if items else "none")

    # no-leading-pipe action table (depends on the md_blocks fix)
    md = "Task | Owner | Status\n--- | --- | ---\nDo X | Sam | Pending"
    items = extract_action_items(md)
    check("ai: no-leading-pipe action table extracted",
          len(items) == 1 and items[0].task == "Do X" and items[0].owner == "Sam",
          str(items[0].__dict__) if items else "none")


# ===========================================================================
def test_ics():
    from mico360.core.calendar_import import parse_ics, format_preamble

    # empty file
    p = _write_ics("")
    d = parse_ics(p); os.unlink(p)
    check("ics: empty file → blank fields, no crash",
          d == {"title": "", "date": "", "attendees": []}, str(d))

    # no VEVENT
    p = _write_ics("BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n")
    d = parse_ics(p); os.unlink(p)
    check("ics: no VEVENT → blank", d["title"] == "" and d["attendees"] == [], str(d))

    # full event: summary with colon, timed DTSTART, CN + mailto attendees, organizer
    ics = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\n"
           "SUMMARY:Budget review: Q3\n"
           "DTSTART:20250714T100000Z\n"
           'ORGANIZER;CN="Alice Boss":mailto:alice@x.com\n'
           'ATTENDEE;CN="Doe, John":mailto:john@x.com\n'
           "ATTENDEE:mailto:mary.jane@x.com\n"
           "END:VEVENT\nEND:VCALENDAR\n")
    p = _write_ics(ics)
    d = parse_ics(p); os.unlink(p)
    check("ics: summary with colon kept whole", d["title"] == "Budget review: Q3", d["title"])
    check("ics: timed DTSTART formatted", d["date"] == "2025-07-14 10:00", d["date"])
    check("ics: organizer first, CN + mailto names parsed",
          d["attendees"] == ["Alice Boss", "Doe, John", "Mary Jane"], str(d["attendees"]))

    # date-only DTSTART (VALUE=DATE) → no time component
    ics = ("BEGIN:VEVENT\nSUMMARY:All day\nDTSTART;VALUE=DATE:20251225\nEND:VEVENT")
    p = _write_ics(ics)
    d = parse_ics(p); os.unlink(p)
    check("ics: date-only DTSTART → date without time", d["date"] == "2025-12-25", d["date"])

    # folded SUMMARY line (RFC 5545 continuation)
    ics = "BEGIN:VEVENT\nSUMMARY:Very long tit\n le continued\nEND:VEVENT"
    p = _write_ics(ics)
    d = parse_ics(p); os.unlink(p)
    check("ics: folded line unfolded", d["title"] == "Very long title continued", repr(d["title"]))

    # duplicate organizer/attendee de-duplicated
    ics = ("BEGIN:VEVENT\nSUMMARY:S\n"
           'ORGANIZER;CN="Sam":mailto:sam@x.com\n'
           'ATTENDEE;CN="Sam":mailto:sam@x.com\nEND:VEVENT')
    p = _write_ics(ics)
    d = parse_ics(p); os.unlink(p)
    check("ics: duplicate organizer/attendee de-duplicated",
          d["attendees"] == ["Sam"], str(d["attendees"]))

    # only the FIRST event is read
    ics = ("BEGIN:VEVENT\nSUMMARY:First\nEND:VEVENT\n"
           "BEGIN:VEVENT\nSUMMARY:Second\nEND:VEVENT")
    p = _write_ics(ics)
    d = parse_ics(p); os.unlink(p)
    check("ics: only first VEVENT used", d["title"] == "First", d["title"])

    # malformed DTSTART must not crash
    ics = "BEGIN:VEVENT\nSUMMARY:S\nDTSTART:not-a-date\nEND:VEVENT"
    p = _write_ics(ics)
    try:
        d = parse_ics(p); ok = True
    except Exception:
        ok = False
    finally:
        os.unlink(p)
    check("ics: malformed DTSTART does not crash", ok)

    # format_preamble
    check("ics: empty preamble is blank", format_preamble({}) == "")
    pre = format_preamble({"title": "T", "date": "", "attendees": []})
    check("ics: preamble includes only real values",
          "Meeting Title: T" in pre and "Attendees" not in pre and "Date" not in pre, repr(pre))


def main() -> int:
    for suite in (test_md_blocks, test_action_items, test_ics):
        try:
            suite()
        except Exception as exc:                      # a raised error = a failed test
            results.append((f"{suite.__name__} raised", False, repr(exc)))
            print(f"  [FAIL] {suite.__name__} raised  {exc!r}")
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n==== PARSERS: {passed}/{len(results)} passed ====")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
