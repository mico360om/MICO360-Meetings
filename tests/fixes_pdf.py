"""PDF Minutes of Meeting: layout, branding, page breaks, nothing lost or clipped.

    python tests/fixes_pdf.py

Reads every exported PDF back with pypdfium2 (text + image positions) and checks:
  P1  all the minutes' words are in the PDF (nothing missing)
  P2  every text line and the logo lie inside the page's printable area
  P3  no two text lines overlap; the logo overlaps no text
  P4  table rows are never split across pages; the header row repeats
  P5  no section heading is stranded at the bottom of a page
  P6  letterhead on page 1, running header on later pages, "Page n of N"
  P7  details panel, attendee grid, colour-coded status, PDF metadata
  P8  hard inputs: long company/contact/footer, tall/wide logos in every
      position, no profile, Arabic, a row taller than a page, a 300-char
      token, 150 attendees, empty minutes, a non-standard template, a pale
      accent colour, page numbers in the header

Runs against a THROWAWAY data folder and needs no network, Ollama or audio.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="mico360_pdf_"))
os.environ["LOCALAPPDATA"] = str(_TMP)
os.environ["XDG_DATA_HOME"] = str(_TMP)
os.environ.setdefault("PYTHONUTF8", "1")
ROOT = Path(os.environ.get("MICO360_TEST_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from mico360 import config                              # noqa: E402

assert str(config.DATA_DIR).startswith(str(_TMP)), f"not isolated: {config.DATA_DIR}"

import pypdfium2 as pdfium                              # noqa: E402
import pypdfium2.raw as pdfium_raw                      # noqa: E402
from PIL import Image                                   # noqa: E402

from mico360.core.profiles import CompanyProfile        # noqa: E402
from mico360.export import md_blocks                    # noqa: E402
from mico360.export import pdf_export as P              # noqa: E402

results: list[tuple[str, bool, str]] = []
WORK = _TMP / "work"
WORK.mkdir(parents=True, exist_ok=True)
MM = 72 / 25.4


def check(name: str, cond, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))


# ----------------------------------------------------------------------------
# reading a PDF back
# ----------------------------------------------------------------------------
class Page:
    def __init__(self, pg, index: int):
        self.index = index
        self.w, self.h = pg.get_size()
        tp = pg.get_textpage()
        self.text = tp.get_text_range()
        self.lines = []                                   # (l, b, r, t, text)
        for k in range(tp.count_rects()):
            l, b, r, t = tp.get_rect(k)
            s = tp.get_text_bounded(l, b, r, t).strip()
            if s:
                self.lines.append((l, b, r, t, s))
        self.images = [o.get_bounds() for o in pg.get_objects(filter=[pdfium_raw.FPDF_PAGEOBJ_IMAGE])]

    @property
    def flat(self) -> str:
        return _norm(self.text)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("­", "")).strip()


def read(path: Path) -> tuple[list[Page], dict]:
    pdf = pdfium.PdfDocument(str(path))
    pages = [Page(pdf[i], i) for i in range(len(pdf))]
    meta = pdf.get_metadata_dict()
    pdf.close()
    return pages, meta


def export(md: str, name: str, profile: CompanyProfile | None = None) -> tuple[list[Page], dict]:
    path = P.export_pdf(md, WORK / f"{name}.pdf", profile)
    return read(path)


def words(s: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Za-z0-9]+", s)]


def missing_words(md: str, pages: list[Page]) -> list[str]:
    """Words of the minutes' text that do not appear in the PDF (by count).
    The "Meeting Title" LABEL is the one thing not printed as such: its value
    is set as the document's title."""
    from collections import Counter
    src = []
    for blk in md_blocks.parse(md):
        key = "" if blk.key.strip().lower() in P._TITLE_KEYS else blk.key
        for t in [blk.text, key, *blk.items, *blk.headers, *(c for r in blk.rows for c in r)]:
            src += words(md_blocks.plain(t))
    have = Counter(w for p in pages for w in words(p.text))
    need = Counter(src)
    return [w for w, n in need.items() if have[w] < n]


def out_of_bounds(pages: list[Page]) -> list[str]:
    bad = []
    for p in pages:
        x0, x1 = 12 * MM, p.w - 12 * MM                  # 18 mm margins, 6 mm tolerance
        for l, b, r, t, s in p.lines:
            if l < x0 or r > x1 or b < 3 * MM or t > p.h - 5 * MM:
                bad.append(f"p{p.index + 1} {s[:30]!r} ({l:.0f},{b:.0f},{r:.0f},{t:.0f})")
        for l, b, r, t in p.images:
            if l < x0 or r > x1 or b < 3 * MM or t > p.h - 5 * MM:
                bad.append(f"p{p.index + 1} image ({l:.0f},{b:.0f},{r:.0f},{t:.0f})")
    return bad


def _overlap(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1.0
    return w * h / small


def overlaps(pages: list[Page]) -> list[str]:
    bad = []
    for p in pages:
        L = p.lines
        for i in range(len(L)):
            for j in range(i + 1, len(L)):
                if _overlap(L[i], L[j]) > 0.25:
                    bad.append(f"p{p.index + 1} {L[i][4][:25]!r} x {L[j][4][:25]!r}")
            for im in p.images:
                if _overlap(L[i], im) > 0.05:
                    bad.append(f"p{p.index + 1} image x {L[i][4][:25]!r}")
    return bad


def _logo(name: str, w: int, h: int) -> Path:
    path = WORK / f"{name}.png"
    Image.new("RGB", (w, h), (139, 30, 30)).save(path)
    return path


def profile(**kw) -> CompanyProfile:
    base = dict(name="MICO360 Technologies LLC", address="Al Khuwair, Muscat, Oman",
                phone="+968 2400 0000", email="info@mico360.com", website="www.mico360.com",
                logo_path=str(ROOT / "assets" / "logo.png"), logo_position="left",
                footer_text="Confidential - for internal use only", accent_color="#8B1E1E")
    base.update(kw)
    return CompanyProfile(**base)


def _task(i: int) -> str:
    return (f"Prepare the revised delivery plan for workstream {i}, covering the migration "
            f"steps, rollback procedure and sign-off from the security review board")


PEOPLE = ["Aisha Al-Balushi (Chair)", "John Smith", "Fatima Al-Harthy", "Rahul Menon",
          "Sara Lopez", "Omar Al-Rawahi", "Priya Nair", "David Chen", "Mariam Al-Zadjali"]


def full_minutes(rows: int = 14, topics: int = 8) -> str:
    discussion = "\n".join(
        f"- **Topic {i}:** The team reviewed item {i} of the programme, including the budget "
        f"impact, the vendor timeline and the dependencies on the infrastructure upgrade."
        for i in range(1, topics + 1))
    actions = "\n".join(
        f"| {_task(i)} | {PEOPLE[i % 4].split(' (')[0]} | {10 + i} October 2026 | "
        f"{['Pending', 'In Progress', 'Done'][i % 3]} |" for i in range(1, rows + 1))
    return f"""# Meeting Minutes
**Meeting Title:** Q4 Programme Steering Committee
**Date and Time:** 29 September 2026, 10:00 - 11:30 (GST)
**Location:** Head Office, Muscat - Board Room 2
**Attendees:** {", ".join(PEOPLE)}

## Purpose of Meeting
To review progress on the Q4 programme and confirm owners for the migration workstreams.

## Meeting Summary
The committee reviewed the status of all workstreams. Two are at risk due to vendor delays.

## Key Discussion Points
{discussion}

## Decisions Made
1. The revised Q4 budget is approved, subject to the finance review.
2. The data-centre migration moves to the weekend of 17 October 2026.

## Action Items
| Task | Responsible Person | Deadline | Status |
| --- | --- | --- | --- |
{actions}

## Pending Issues
- Vendor contract renewal terms are still under negotiation.

## Risks or Concerns
- Not specified

## Next Meeting Notes
Next meeting on 13 October 2026 at 10:00.

## Closing Summary
The chair closed the meeting at 11:30.
"""


def basic_checks(label: str, md: str, pages: list[Page]) -> None:
    miss = missing_words(md, pages)
    check(f"{label}: every word of the minutes is in the PDF", not miss, str(miss[:12]))
    oob = out_of_bounds(pages)
    check(f"{label}: all text and images inside the printable area", not oob, str(oob[:4]))
    ov = overlaps(pages)
    check(f"{label}: no overlapping text (or text under the logo)", not ov, str(ov[:4]))
    total = len(pages)
    nums = all(f"Page {p.index + 1} of {total}" in p.flat for p in pages)
    check(f"{label}: 'Page n of {total}' on every page", nums)


# =============================================================================
def test_full_minutes() -> None:
    print("\n[P1-P7] a full Minutes of Meeting with a company profile")
    md = full_minutes()
    pages, meta = export(md, "full", profile())
    basic_checks("full", md, pages)
    check("spans several pages (the test exercises page breaks)", len(pages) >= 3, str(len(pages)))

    # P4 — every action row whole on one page
    split = [i for i in range(1, 15) if not any(_norm(_task(i)) in p.flat for p in pages)]
    check("P4: no action-item row is split across pages", not split, f"rows {split}")
    table_pages = [p for p in pages if any(f"workstream {i}," in p.text for i in range(1, 15))]
    check("P4: the table header row repeats on every page the table spans",
          len(table_pages) >= 2 and all("Responsible Person" in p.flat for p in table_pages),
          f"{len(table_pages)} pages")

    # P5 — a heading always has content below it on the same page
    heads = ["Purpose of Meeting", "Meeting Summary", "Key Discussion Points", "Decisions Made",
             "Action Items", "Pending Issues", "Risks or Concerns", "Next Meeting Notes",
             "Closing Summary"]
    stranded = []
    for h in heads:
        for p in pages:
            hit = [ln for ln in p.lines if ln[4] == h]
            if hit:
                below = [ln for ln in p.lines if ln[3] < hit[0][1] - 1 and ln[1] > 17 * MM]
                if not below:
                    stranded.append(f"{h} (p{p.index + 1})")
    check("P5: no section heading stranded at a page bottom", not stranded, str(stranded))
    check("P5: every section heading is in the PDF",
          all(any(h in p.flat for p in pages) for h in heads))

    # P6 — letterhead / running header
    p1 = pages[0]
    check("P6: page 1 has the logo and the company name + contact lines",
          len(p1.images) == 1 and "MICO360 Technologies LLC" in p1.flat
          and "info@mico360.com" in p1.flat and "Al Khuwair" in p1.flat)
    logo_bottom = p1.images[0][1] if p1.images else 0
    body_top = max((ln[3] for ln in p1.lines if ln[4] == "MEETING MINUTES"), default=1e9)
    check("P6: the body starts below the letterhead (logo never over content)",
          body_top < logo_bottom, f"body top {body_top:.0f} vs logo bottom {logo_bottom:.0f}")
    later = pages[1:]
    check("P6: later pages carry a running header (company + meeting title), no logo",
          all("MICO360 Technologies LLC" in p.flat and "Q4 Programme Steering Committee" in p.flat
              and not p.images for p in later))
    check("P6: the footer text is on every page",
          all("Confidential - for internal use only" in p.flat for p in pages))

    # P7 — details, attendees, status, metadata
    check("P7: the meeting title is the document title; the H1 is its label",
          "MEETING MINUTES" in p1.flat and any(ln[4] == "Q4 Programme Steering Committee"
                                               for ln in p1.lines))
    check("P7: details panel holds date/time and location",
          "Date and Time" in p1.flat and "Location" in p1.flat and "Board Room 2" in p1.flat)
    check("P7: attendees shown as a counted name grid",
          "Attendees (9)" in p1.flat and all(n in p1.flat for n in PEOPLE))
    grid = [ln for ln in p1.lines if ln[4] in ("John Smith", "Fatima Al-Harthy")]
    check("P7: attendee names sit side by side (a grid, not one long line)",
          len(grid) == 2 and abs(grid[0][1] - grid[1][1]) < 2 and abs(grid[0][0] - grid[1][0]) > 60)
    check("P7: statuses keep their wording", all(any(s in p.flat for p in pages)
                                                  for s in ("Pending", "In Progress", "Done")))
    check("P7: PDF metadata names the meeting and company",
          meta.get("Title") == "Q4 Programme Steering Committee"
          and meta.get("Author") == "MICO360 Technologies LLC"
          and meta.get("Subject") == "Minutes of Meeting")


def test_status_colours() -> None:
    print("\n[P7] status colours and column widths")
    from reportlab.lib import colors
    S = P._styles(colors.HexColor("#8B1E1E"), colors.white)
    for raw, want in (("Done", "#16A34A"), ("In Progress", "#B8760F"), ("pending", "#6C6269"),
                      ("Cancelled", "#8A8088")):
        _k, markup = P._cell_markup(raw, S, True)
        check(f"status '{raw}' is coloured {want}", markup is not None and want in markup, str(markup))
    _k, markup = P._cell_markup("Waiting on vendor", S, True)
    check("an unknown status is kept as plain text", markup is None)
    _k, markup = P._cell_markup("Not specified", S, False)
    check("'Not specified' is shown muted", markup is not None and "#6C6269" in markup)
    heads = ["Task", "Responsible Person", "Deadline", "Status"]
    rows = [[_task(1), "Fatima Al-Harthy", "12 October 2026", "In Progress"]]
    w = P._col_widths(heads, rows, P.CONTENT_W, 3)
    fits = all(w[j] - P._CELL_PAD >= P._text_w(t, "Helvetica-Bold" if j == 3 else "Helvetica", 9)
               + (P._STATUS_MARK if j == 3 else 0) - 0.5
               for j, t in ((1, "Fatima Al-Harthy"), (2, "12 October 2026"), (3, "In Progress")))
    check("short columns (person, date, status) get their one-line width", fits,
          str([round(x) for x in w]))
    check("the long Task column takes the remaining width",
          w[0] == max(w) and abs(sum(w) - P.CONTENT_W) < 0.5, str([round(x) for x in w]))
    many = P._col_widths([f"Column {i}" for i in range(12)],
                         [[f"value{i}xyz" for i in range(12)]], P.CONTENT_W)
    check("12 columns still fit the page width", abs(sum(many) - P.CONTENT_W) < 0.5)


def test_hard_profiles() -> None:
    print("\n[P8] long letterhead text, logos of every shape and position")
    md = full_minutes(rows=4, topics=2)
    long_prof = profile(
        name="The Very Long International Holding Company for Industrial Services and "
             "Engineering Consultancy (Muscat Branch) LLC",
        address="Building 1234, Way 5678, Block 910, Al Khuwair South, Bawshar, Muscat "
                "Governorate, Sultanate of Oman, P.O. Box 1234, Postal Code 112",
        phone="+968 2400 0000 / +968 9999 9999 ext. 12345",
        email="executive.office.secretariat@very-long-company-domain-name.com",
        website="https://www.very-long-company-domain-name.com/about-us/offices/muscat",
        footer_text="Confidential and proprietary. This document contains information that is "
                    "the property of the company and may not be copied, distributed or "
                    "disclosed to any third party without prior written consent. " * 2)
    pages, _ = export(md, "long_profile", long_prof)
    basic_checks("long company/contact/footer", md, pages)
    fw = words(long_prof.footer_text)
    check("a very long footer is printed in full on every page (wrapped, not clipped)",
          all(not [w for w in set(fw) if w not in words(p.text)] for p in pages))
    check("long contact details all printed (wrapped, not clipped)",
          all(s in _norm(pages[0].text) for s in ("Postal Code 112", "ext. 12345",
                                                   "very-long-company-domain-name.com")))
    for shape, (w, h) in (("tall", (120, 480)), ("wide", (1600, 90)), ("square", (400, 400))):
        logo = _logo(shape, w, h)
        for pos in ("left", "center", "right"):
            pages, _ = export(md, f"logo_{shape}_{pos}",
                              profile(logo_path=str(logo), logo_position=pos, logo_width_mm=70))
            oob, ov = out_of_bounds(pages), overlaps(pages)
            check(f"{shape} logo, {pos}: inside the page and over no text", not oob and not ov,
                  str((oob + ov)[:3]))
    pages, _ = export(md, "missing_logo", profile(logo_path=str(WORK / "no_such_logo.png")))
    check("a missing logo file still exports (name + contact only)",
          not pages[0].images and "MICO360 Technologies LLC" in pages[0].flat)
    pages, _ = export(md, "hdr_numbers", profile(page_number_position="header-right",
                                                 footer_alignment="left"))
    check("page numbers in the header: present and not overlapping",
          all(f"Page {p.index + 1} of {len(pages)}" in p.flat for p in pages)
          and not overlaps(pages))
    pages, _ = export(md, "same_side", profile(page_number_position="footer-center",
                                               footer_alignment="center"))
    check("footer text and page number on the same side don't collide", not overlaps(pages))
    pages, _ = export(md, "no_numbers", profile(show_page_numbers=False))
    check("page numbers can be switched off", not any("Page 1 of" in p.flat for p in pages))


def test_no_profile_and_pale_accent() -> None:
    print("\n[P8] no company profile; a pale accent colour")
    md = full_minutes(rows=6, topics=3)
    pages, meta = export(md, "no_profile", None)
    basic_checks("no profile", md, pages)
    check("no profile: no letterhead, still titled", not pages[0].images
          and "Q4 Programme Steering Committee" in pages[0].flat
          and meta.get("Author") == "MICO360 Meetings")
    from reportlab.lib import colors
    pale = colors.HexColor("#FFE066")
    ink = P._readable(pale)
    check("a pale accent is darkened for headings (contrast >= 4.5)",
          P._contrast(ink, colors.white) >= 4.5, f"{P._contrast(ink, colors.white):.2f}")
    pages, _ = export(md, "pale", profile(accent_color="#FFE066"))
    check("a pale accent still exports cleanly", not overlaps(pages) and not out_of_bounds(pages))


def test_arabic() -> None:
    print("\n[P8] Arabic minutes")
    md = ("# محضر الاجتماع\n**عنوان الاجتماع:** اجتماع اللجنة التوجيهية\n"
          "**التاريخ:** ٢٩ سبتمبر ٢٠٢٦\n**الحضور:** عائشة البلوشية، أحمد الحارثي، فاطمة الرواحي\n\n"
          "## القرارات\n1. الموافقة على الميزانية\n2. مراجعة العقود\n\n"
          "## بنود العمل\n| المهمة | المسؤول | الموعد | الحالة |\n| - | - | - | - |\n"
          "| إعداد خطة التسليم المعدلة | أحمد | ١٠ أكتوبر | قيد التنفيذ |\n")
    pages, meta = export(md, "arabic", profile(name="شركة ميكو للتقنية"))
    txt = "".join(p.text for p in pages)
    check("Arabic: exported with Arabic glyphs", any("؀" <= c <= "ۿ" or "ﹰ" <= c <= "﻿"
                                                     for c in txt))
    check("Arabic: inside the page, no overlaps", not out_of_bounds(pages) and not overlaps(pages),
          str((out_of_bounds(pages) + overlaps(pages))[:3]))
    check("Arabic: metadata title is the meeting title", meta.get("Title") == "اجتماع اللجنة التوجيهية",
          str(meta.get("Title")))
    p1 = pages[0]
    labels = [ln for ln in p1.lines if ln[1] < 700]
    right_side = [ln for ln in labels if ln[0] > p1.w / 2]
    check("Arabic: the details panel is mirrored (labels on the right)", bool(right_side))


def test_hard_content() -> None:
    print("\n[P8] a row taller than a page, a 300-char token, 150 attendees, odd templates")
    huge = " ".join(f"word{i}" for i in range(1500))
    md = ("# Meeting Minutes\n**Meeting Title:** Big row\n\n## Action Items\n"
          "| Task | Owner | Status |\n| --- | --- | --- |\n"
          f"| {huge} | John | Pending |\n| Short follow-up task | Sara | Done |\n")
    try:
        pages, _ = export(md, "huge_row", profile())
        ok = True
    except Exception as exc:                                # LayoutError before the fix
        pages, ok = [], False
        print("   ", repr(exc))
    check("a table row taller than a page exports (it may split, as it must)", ok)
    if ok:
        basic_checks("huge row", md, pages)

    token = "https://example.com/" + "a1b2c3d4e5" * 28
    md = (f"# Meeting Minutes\n**Meeting Title:** Links\n\n## Notes\nSee {token} for details.\n\n"
          f"## Action Items\n| Task | Link |\n| --- | --- |\n| Review | {token} |\n")
    pages, _ = export(md, "token", profile())
    oob = out_of_bounds(pages)
    check("a 300-character unbroken token stays inside the page", not oob, str(oob[:2]))
    joined = "".join(re.sub(r"\s+", "", p.text) for p in pages)
    check("…and every character of it is printed", joined.count(re.sub(r"\s", "", token)) >= 2)

    names = [f"Participant Number {i:03d}" for i in range(1, 151)]
    md = ("# Meeting Minutes\n**Meeting Title:** Town hall\n**Attendees:** " + ", ".join(names)
          + "\n\n## Summary\nAll-hands meeting.\n")
    pages, _ = export(md, "attendees150", profile())
    flat = " ".join(p.flat for p in pages)
    check("150 attendees: every name printed (the grid flows across pages)",
          all(n in flat for n in names) and "Attendees (150)" in flat, f"{len(pages)} pages")
    check("150 attendees: no overlaps / clipping", not overlaps(pages) and not out_of_bounds(pages))
    check("150 attendees: the list starts on page 1 (no blank gap before a long grid)",
          "Attendees (150)" in pages[0].flat and "Participant Number 001" in pages[0].flat)

    rows = "\n".join(f"| Task number {i} | Owner {i} | Pending |" for i in range(1, 61))
    md = ("# Meeting Minutes\n**Meeting Title:** Phases\n\n## Summary\nShort summary.\n\n"
          "## Action Items\n### Phase 1\n| Task | Owner | Status |\n| --- | --- | --- |\n" + rows + "\n")
    pages, _ = export(md, "h3_long_table", profile())
    check("a sub-heading before a table longer than a page stays on page 1 with its first rows",
          "Phase 1" in pages[0].flat and "Task number 1 " in pages[0].flat + " ", f"{len(pages)} pages")
    check("…and the long table's rows are all printed", not missing_words(md, pages))

    import time
    sections = "\n\n".join(f"## Topic {s}\n" + "\n".join(
        f"- Point {s}.{i}: discussion of item {i}, budget and timeline." for i in range(10))
        for s in range(40))
    rows = "\n".join(f"| Task {i}: prepare the workstream {i} report | Owner {i % 7} | "
                     f"{1 + i % 28} Oct 2026 | Pending |" for i in range(400))
    md = (f"# Meeting Minutes\n**Meeting Title:** Long meeting\n\n{sections}\n\n## Action Items\n"
          f"| Task | Owner | Deadline | Status |\n| --- | --- | --- | --- |\n{rows}\n")
    t0 = time.time()
    pages, _ = export(md, "long_meeting", profile())
    took = time.time() - t0
    check("a very long meeting (400 points + 400 action rows) exports quickly with all its text",
          took < 20 and not missing_words(md, pages), f"{took:.1f}s, {len(pages)} pages")

    for label, md in (("empty minutes", ""), ("heading only", "# Meeting Minutes"),
                      ("TL;DR template (no title, no details)",
                       "Quick recap of the sync.\n\n- Ship v2 on Friday\n- Sara owns QA\n\n"
                       "**Decision:** go ahead")):
        try:
            pages, _ = export(md, re.sub(r"\W+", "_", label), profile())
            ok = len(pages) >= 1 and not overlaps(pages) and not missing_words(md, pages)
        except Exception as exc:
            ok = False
            print("   ", label, repr(exc))
        check(f"{label}: exports cleanly with all its text", ok)

    md = ("# Meeting Minutes\n**Meeting Title:** A & B <Review>\n**Attendees:** Not specified\n\n"
          "## Summary\nText with <tags> & ampersands.\n")
    pages, meta = export(md, "escaping", profile(name="Evil <b>Co</b> & Sons"))
    flat = " ".join(p.flat for p in pages)
    check("markup characters are printed literally (title, body, company)",
          "A & B <Review>" in flat and "<tags> & ampersands" in flat and "Evil <b>Co</b> & Sons" in flat)
    check("'Attendees: Not specified' shown without a count", "Attendees" in flat
          and "Attendees (" not in flat and "Not specified" in flat)


def _fonts(path: Path) -> list[tuple[str, bool]]:
    """(BaseFont, embedded?) for every font used on every page."""
    from pypdf import PdfReader
    out = []
    for page in PdfReader(str(path)).pages:
        fonts = (page.get("/Resources") or {}).get("/Font") or {}
        for ref in fonts.values():
            f = ref.get_object()
            desc = f.get("/FontDescriptor")
            if desc is None and "/DescendantFonts" in f:
                desc = f["/DescendantFonts"][0].get_object().get("/FontDescriptor")
            desc = desc.get_object() if desc is not None else {}
            emb = any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3"))
            out.append((str(f.get("/BaseFont")), emb))
    return out


def test_unicode_and_inline() -> None:
    print("\n[P9] every script prints (no black boxes); inline Markdown; embedded fonts")
    names = ["Łukasz Dvořák", "José Müller", "Ольга Петрова", "张伟", "Nguyễn Văn An",
             "Zoë Brontë", "Γιώργος Παπαδόπουλος", "김민준", "田中ゆき"]
    md = ("# Meeting Minutes\n**Meeting Title:** Global Partners Sync – Q4 “Kick-off”\n"
          f"**Attendees:** {', '.join(names)}\n\n## Meeting Summary\n"
          "Budget of ₹ 5,00,000 and € 12,000 approved ✅ at 25°C ± 2. See *italic note*, "
          "_underscored_, `config.yaml`, ~~Old item~~ and [the plan](https://example.com/plan).\n"
          "Keep snake_case_names and 2*3*4 as written.\n\n"
          "## Action Items\n| Task | Responsible Person | Deadline | Status |\n| --- | --- | --- | --- |\n"
          "| Review contract ✓ | Łukasz Dvořák | 5 Oct | Pending |\n"
          "| Translate deck | Ольга Петрова | 7 Oct | Done |\n"
          "| 检查预算 | 张伟 | 9 Oct | In Progress |\n")
    prof = profile(name="Société Générale Łódź Sp. z o.o.", address="ul. Piotrkowska 1, Łódź",
                   footer_text="Poufne – tylko do użytku wewnętrznego")
    path = P.export_pdf(md, WORK / "unicode.pdf", prof)
    pages, _ = read(path)
    txt = " ".join(p.flat for p in pages)
    check("no character replaced by a black box", "■" not in txt)
    missing = [n for n in names if n not in txt]
    check("names in Latin-extended, Cyrillic, Greek, Chinese, Korean, Japanese and "
          "Vietnamese all printed exactly", not missing, str(missing))
    check("₹ € ✅ ✓ ° ± and typographic quotes printed",
          all(c in txt for c in "₹€✅✓°±“”–"))
    check("letterhead and footer print non-Latin-1 text (Łódź, użytku)",
          "Société Générale Łódź Sp. z o.o." in txt and "użytku wewnętrznego" in txt)
    check("inline Markdown is formatted, not printed as symbols",
          "*italic note*" not in txt and "`" not in txt and "~~" not in txt and "](" not in txt
          and "italic note" in txt and "underscored" in txt and "Old item" in txt)
    check("underscores and asterisks inside words are kept",
          "snake_case_names" in txt and "2*3*4" in txt)
    check("a link prints its label AND its address (survives printing)",
          "the plan (https://example.com/plan)" in txt)
    from pypdf import PdfReader
    uris = [a.get_object().get("/A", {}).get("/URI")
            for pg in PdfReader(str(path)).pages for a in (pg.get("/Annots") or [])]
    check("the link is clickable in the PDF", "https://example.com/plan" in uris, str(uris))
    fonts = _fonts(path)
    check("every font in the PDF is embedded (renders the same on any device/viewer)",
          fonts and all(e for _n, e in fonts), str([n for n, e in fonts if not e]))
    for label, pth in (("full minutes", P.export_pdf(full_minutes(3, 2), WORK / "f.pdf", profile())),
                       ("no profile", P.export_pdf(full_minutes(3, 2), WORK / "n.pdf", None))):
        f = _fonts(pth)
        check(f"{label}: every font embedded", f and all(e for _n, e in f), str(f))
    hebrew = "מטרת הפגישה הייתה לסקור את הפרויקט"
    pages, _ = export(f"# Meeting Minutes\n\n## Notes\n{hebrew}\n", "hebrew", profile())
    heb_line = [ln for ln in pages[0].lines if any("֐" <= c <= "׿" for c in ln[4])]
    check("Hebrew is right-aligned like Arabic (a right-to-left script)",
          heb_line and heb_line[0][2] > pages[0].w - 20 * MM, str(heb_line[:1]))


def test_save_service() -> None:
    print("\n[P10] saving the file: atomic, readable errors, safe names")
    from mico360.export import service
    check("safe file names from meeting titles",
          service.safe_filename('Q4: Plan/Review? <draft>') == "Q4 Plan Review draft"
          and service.safe_filename("CON") == "_CON" and service.safe_filename("  ") == "Meeting-Minutes"
          and service.safe_filename("Minutes...") == "Minutes"
          and len(service.safe_filename("x" * 300)) == 100
          and service.safe_filename("اجتماع اللجنة") == "اجتماع اللجنة")
    folder = WORK / "save"
    folder.mkdir(exist_ok=True)
    target = folder / "Minutes.pdf"
    service.export(full_minutes(2, 1), target, profile())
    first = target.read_bytes()
    check("export writes a real PDF", first[:5] == b"%PDF-" and b"%%EOF" in first[-64:])

    def leftovers() -> list[str]:
        return [p.name for p in folder.iterdir() if ".tmp" in p.name]

    orig = service.EXPORTERS[".pdf"]

    def broken(md, path, prof=None):
        Path(path).write_bytes(b"%PDF-1.4 half written")
        raise RuntimeError("renderer crashed")
    service.EXPORTERS[".pdf"] = broken
    try:
        service.export("x", target, profile())
        crashed = False
    except RuntimeError:
        crashed = True
    finally:
        service.EXPORTERS[".pdf"] = orig
    check("an export that fails half-way leaves the previous file intact",
          crashed and target.read_bytes() == first and not leftovers(), str(leftovers()))

    if sys.platform == "win32":
        import win32file
        h = win32file.CreateFile(str(target), win32file.GENERIC_READ, 0, None,
                                 win32file.OPEN_EXISTING, 0, None)     # share mode 0 = like Acrobat
        try:
            try:
                service.export(full_minutes(2, 1), target, profile())
                msg = ""
            except service.ExportError as exc:
                msg = str(exc)
        finally:
            h.Close()
        check("a PDF held open by a viewer: a plain message, old file intact, no temp left",
              "open in another program" in msg and target.read_bytes() == first and not leftovers(),
              msg or "no error")
        os.chmod(target, 0o444)
        try:
            try:
                service.export(full_minutes(2, 1), target, profile())
                msg = ""
            except service.ExportError as exc:
                msg = str(exc)
        finally:
            os.chmod(target, 0o666)
        check("a read-only file: says so", "read-only" in msg, msg)
    try:
        service.export("x", folder / "missing" / "a.pdf", profile())
        msg = ""
    except service.ExportError as exc:
        msg = str(exc)
    check("a folder that doesn't exist: says so", "doesn't exist" in msg, msg)
    service.export(full_minutes(2, 1), target, profile())
    check("overwriting an existing export works", target.read_bytes()[:5] == b"%PDF-")


def test_export_and_email_ui() -> None:
    print("\n[P11] Export… and Email… in the app")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import threading
    import time
    from PySide6.QtCore import QThread, Signal
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    from mico360.core import ollama_client as OC
    OC.check_status = lambda *a, **k: OC.OllamaStatus(running=False, models=[], error="test")
    from mico360.ui import dialogs as D
    from mico360.ui import pages as PG
    from mico360.ui import workers as W
    from mico360.ui.context import AppContext

    def wait_for(cond, timeout=20.0):
        end = time.time() + timeout
        while time.time() < end:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return bool(cond())

    class Toast:
        def __init__(self):
            self.calls = []

        def show_message(self, text, kind="info", msec=0, links=None):
            self.calls.append((text, kind, dict(links or {})))

    ctx = AppContext()
    toast = Toast()
    page = PG.NewMeetingPage(ctx, toast)
    _KEEP.extend([ctx, page])
    page.minutes.setPlainText(full_minutes(3, 2))
    page.meeting_title.setText("Q4: Plan/Review?")
    out_dir = WORK / "exports ÷ Łódź"            # unicode folder name
    out_dir.mkdir(exist_ok=True)
    seen = {}

    def fake_save(parent, caption, start, filters, selected=""):
        seen.setdefault("calls", []).append((start, selected))
        return str(out_dir / Path(start).name), selected or "PDF Document (*.pdf)"
    orig_save = PG.QFileDialog.getSaveFileName
    orig_crit = QMessageBox.critical
    crit = []
    QMessageBox.critical = staticmethod(lambda *a, **k: crit.append(a[2] if len(a) > 2 else ""))
    PG.QFileDialog.getSaveFileName = staticmethod(fake_save)
    try:
        page._export()
        ok = wait_for(lambda: page.export_btn.isEnabled())
        start, sel = seen["calls"][0]
        check("Save dialog starts in a real folder (not the app's install folder), "
              "named after the meeting",
              Path(start).parent.is_dir() and Path(start).parent != Path.cwd()
              and Path(start).name == "Q4 Plan Review", start)
        check("PDF is the default format", sel == "PDF Document (*.pdf)", sel)
        pdf = out_dir / "Q4 Plan Review.pdf"
        check("the PDF is saved where the user chose (unicode folder name)",
              ok and pdf.exists() and pdf.read_bytes()[:5] == b"%PDF-", str(list(out_dir.iterdir())))
        text, kind, links = toast.calls[-1] if toast.calls else ("", "", {})
        check("success message offers Open and Show in folder",
              kind == "success" and "Q4 Plan Review.pdf" in text
              and set(links) == {"Open", "Show in folder"}, str(toast.calls[-1:]))
        check("the folder is remembered for next time",
              Path(ctx.settings.get("last_export_dir")) == out_dir)
        page._export()
        wait_for(lambda: page.export_btn.isEnabled())
        check("next export starts in the remembered folder",
              Path(seen["calls"][1][0]).parent == out_dir, seen["calls"][1][0])
        if sys.platform == "win32":
            import win32file
            before = pdf.read_bytes()
            h = win32file.CreateFile(str(pdf), win32file.GENERIC_READ, 0, None,
                                     win32file.OPEN_EXISTING, 0, None)
            try:
                page._export()
                wait_for(lambda: page.export_btn.isEnabled())
            finally:
                h.Close()
            check("PDF open in a viewer: the app explains what to do; old file kept",
                  crit and "open in another program" in crit[-1] and pdf.read_bytes() == before
                  and not [p for p in out_dir.iterdir() if ".tmp" in p.name], str(crit[-1:]))
    finally:
        PG.QFileDialog.getSaveFileName = orig_save
        QMessageBox.critical = orig_crit

    # Email: the attachment is a real PDF named after the meeting; cleaned up after
    class FakeEmail(QThread):
        finished_ok = Signal()
        failed = Signal(str)
        made = []

        def __init__(self, cfg, to, subject, body, html=None, attachments=None, cc=None):
            super().__init__()
            self.attachments = list(attachments or [])
            self.present = []
            FakeEmail.made.append(self)

        def run(self):
            self.present = [(Path(a).name, Path(a).read_bytes()[:5]) for a in self.attachments]
            self.finished_ok.emit()
    ctx.settings.set("smtp_host", "smtp.example.com"); ctx.settings.set("smtp_user", "u")
    ctx.settings.set("smtp_password", "p"); ctx.settings.set("email_from", "me@example.com")
    orig_exec, orig_worker = D.EmailComposeDialog.exec, W.EmailWorker

    def fake_exec(dlg):
        dlg.to.setText("someone@example.com")
        dlg.att_pdf.setChecked(True)
        dlg.att_docx.setChecked(False)
        return 1
    D.EmailComposeDialog.exec = fake_exec
    W.EmailWorker = FakeEmail
    try:
        page._email_minutes()
        wait_for(lambda: FakeEmail.made and not FakeEmail.made[-1].isRunning()
                 and page.email_btn.isEnabled())
    finally:
        D.EmailComposeDialog.exec, W.EmailWorker = orig_exec, orig_worker
    em = FakeEmail.made[-1] if FakeEmail.made else None
    check("e-mail attaches the branded PDF, named after the meeting",
          em is not None and em.present == [("Q4 Plan Review.pdf", b"%PDF-")],
          str(em.present if em else None))
    check("the e-mail's temporary attachment is removed once sent",
          em is not None and em.attachments and not Path(em.attachments[0]).parent.exists())
    del threading


_KEEP: list = []


def test_service_route() -> None:
    print("\n[P1] the app's export service uses this layout")
    from mico360.export import service
    path = service.export(full_minutes(rows=3, topics=2), WORK / "service.pdf", profile())
    pages, meta = read(Path(path))
    check("service.export(.pdf) produces the branded MoM",
          meta.get("Subject") == "Minutes of Meeting" and "Attendees (9)" in pages[0].flat)


# =============================================================================
def main() -> int:
    for fn in (test_full_minutes, test_status_colours, test_hard_profiles,
               test_no_profile_and_pale_accent, test_arabic, test_hard_content,
               test_unicode_and_inline, test_save_service, test_export_and_email_ui,
               test_service_route):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} ran without errors", False, repr(exc))
    passed = sum(ok for _, ok, _ in results)
    print(f"\n==== PDF: {passed}/{len(results)} passed ====")
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
