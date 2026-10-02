"""Templates, import/export, per-company PDF designs and branding.

    python tests/fixes_templates.py

  T1  prompt templates: export/import keeps the text character-for-character;
      nothing is overwritten; copies and edits; restore a built-in; atomic saves
  T2  company profiles: JSON / CSV / Excel export -> import on "another PC"
      keeps every field, the PDF design and the logo; duplicates; bad files
  T3  the four PDF designs: each passes the full layout checks; they differ;
      the design is the company's, saved independently
  T4  the app: Settings -> PDF design, the Company branding selector on the
      Minutes step, exports and e-mails branded for the right company

Runs against a THROWAWAY data folder (set up by fixes_pdf, whose PDF-reading
helpers this reuses) and needs no network, Ollama or audio hardware.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixes_pdf as FP                                   # noqa: E402  (isolates LOCALAPPDATA)

from mico360.core.profiles import CompanyProfile, ProfileStore   # noqa: E402
from mico360.core.prompts import BUILTIN_PROMPTS, PromptLibrary  # noqa: E402
from mico360.export import pdf_designs, service                  # noqa: E402
from mico360.export import pdf_export as P                       # noqa: E402

check, results, WORK, MM = FP.check, FP.results, FP.WORK, FP.MM
ROOT = FP.ROOT
_KEEP: list = []

TRICKY = ("You are a meeting secretary.\n\tIndented with a tab\ttwo tabs  \n\n"
          "## Structure\n| Task | Owner |\n| --- | --- |\n"
          "Arabic: محضر الاجتماع — emoji ✅ — quotes “smart” 'single' \"double\"\n"
          "   leading and trailing spaces   \n\\backslash \\n literal, {braces}, 100% & <tags>\n"
          "[TRANSCRIPT_HERE]\n\n\nEnd with blank lines above.\n")


def _logo(name: str, colour=(139, 30, 30), size=(240, 100)) -> Path:
    from PIL import Image
    path = WORK / "logos_src" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)
    return path


# =============================================================================
def test_prompt_templates() -> None:
    print("\n[T1] prompt templates: import / export / copy / edit")
    lib = PromptLibrary(WORK / "prompts_a")
    builtins = len(lib.list())
    check("the library starts with every built-in prompt", builtins == len(BUILTIN_PROMPTS))
    a = lib.add("Board pack", TRICKY, category="My Templates")
    lib.set_favorite(a.id, True)
    b = lib.add("Short one", "Summarise.\n[TRANSCRIPT_HERE]", category="General")
    exp = WORK / "prompts export.json"
    lib.export_file([lib.get(a.id), lib.get(b.id)], exp)
    data = json.loads(exp.read_text(encoding="utf-8"))
    check("the export is readable JSON holding the prompts' text",
          [r["name"] for r in data["prompts"]] == ["Board pack", "Short one"]
          and data["prompts"][0]["text"] == TRICKY)

    other = PromptLibrary(WORK / "prompts_b")            # "another PC"
    before = {f.name: f.read_bytes() for f in other.dir.glob("*.json")}
    res = other.import_file(exp)
    got = {p.name: p for p in res.imported}
    check("import brings every prompt across", sorted(got) == ["Board pack", "Short one"],
          res.summary())
    bp = got.get("Board pack")
    check("the text is identical character-for-character (tabs, spaces, blank lines, "
          "Arabic, emoji, symbols)", bp is not None and bp.text == TRICKY,
          repr(bp.text[:60]) if bp else "")
    check("category and favourite are kept; imported prompts are Custom",
          bp is not None and bp.category == "My Templates" and bp.favorite and not bp.builtin)
    after = {f.name: f.read_bytes() for f in other.dir.glob("*.json")}
    check("importing never changes a prompt that was already there",
          all(after.get(k) == v for k, v in before.items()))
    again = other.import_file(exp)
    check("importing the same file again adds nothing (identical prompts are skipped)",
          not again.imported and len(again.skipped) == 2, again.summary())
    lib.update(a.id, "Board pack", TRICKY + "\nextra line", category="My Templates")
    lib.export_file([lib.get(a.id)], exp)
    third = other.import_file(exp)
    check("a different prompt with the same name is added as “Board pack (2)”, not overwritten",
          [p.name for p in third.imported] == ["Board pack (2)"]
          and other.get(bp.id).text == TRICKY, third.summary())

    (WORK / "My_custom prompt.md").write_bytes(           # BOM + Windows line endings
        b"\xef\xbb\xbfLine 1\r\nLine 2\r\n[TRANSCRIPT_HERE]\r\n")
    one = other.import_file(WORK / "My_custom prompt.md")
    check("a .md / .txt file imports as one prompt named after the file (BOM and CRLF handled)",
          len(one.imported) == 1 and one.imported[0].name == "My custom prompt"
          and one.imported[0].text == "Line 1\nLine 2\n[TRANSCRIPT_HERE]\n", one.summary())
    (WORK / "mixed.json").write_text(json.dumps(
        [{"name": "OK", "text": "body [TRANSCRIPT_HERE]"}, {"name": "No text"}, "junk",
         {"name": "Blank", "text": "   "}, {"text": "nameless [TRANSCRIPT_HERE]", "category": 7}]),
        encoding="utf-8")
    mixed = other.import_file(WORK / "mixed.json")
    check("entries without usable text are reported, the rest import",
          sorted(p.name for p in mixed.imported) == ["Imported prompt", "OK"] and mixed.invalid == 3,
          mixed.summary())
    for bad, label in ((WORK / "x.docx", "an unsupported file type"),
                       (WORK / "broken.json", "a broken JSON file")):
        bad.write_text("{not json", encoding="utf-8")
        n = len(other.list())
        try:
            other.import_file(bad)
            raised = False
        except Exception:
            raised = True
        check(f"{label} is refused and the library is unchanged", raised and len(other.list()) == n)

    d1, d2 = lib.duplicate(a.id), lib.duplicate(a.id)
    check("Duplicate copies the text exactly and names the copies “(copy)”, “(copy 2)”",
          d1.text == lib.get(a.id).text and (d1.name, d2.name) == ("Board pack (copy)",
                                                                   "Board pack (copy 2)"))
    fm = next(p for p in lib.list() if p.name == "Formal Minutes")
    dup = lib.duplicate(fm.id)
    check("a copy of a built-in is an editable Custom prompt", not dup.builtin and dup.text == fm.text)
    lib.update(a.id, "Board pack v2", "new [TRANSCRIPT_HERE]", category="Other")
    ed = lib.get(a.id)
    check("editing keeps the prompt's identity and favourite, and changes only what was edited",
          ed.id == a.id and ed.favorite and (ed.name, ed.text, ed.category)
          == ("Board pack v2", "new [TRANSCRIPT_HERE]", "Other"))
    lib.update(fm.id, fm.name, fm.text + "\n(edited)")
    check("an edited built-in is detected", lib.is_modified(lib.get(fm.id)))
    lib.restore_builtin(fm.id)
    check("Restore default puts the built-in's original text back",
          lib.get(fm.id).text == fm.text and not lib.is_modified(lib.get(fm.id)))

    target = lib.dir / f"{a.id}.json"
    good = target.read_bytes()
    real = os.replace
    os.replace = lambda *a_, **k: (_ for _ in ()).throw(OSError("disk full"))
    try:
        try:
            lib.update(a.id, "Board pack v3", "x [TRANSCRIPT_HERE]")
            failed = False
        except OSError:
            failed = True
    finally:
        os.replace = real
    check("a save that fails leaves the prompt file intact and no temp file behind",
          failed and target.read_bytes() == good
          and not [f for f in lib.dir.iterdir() if f.name.endswith(".tmp")])
    reopened = PromptLibrary(lib.dir)
    check("reopening the library re-seeds nothing and loses nothing",
          len(reopened.list()) == len(lib.list()))


# =============================================================================
def _full_profile(store: ProfileStore, name: str = "Alpha Industries — ألفا") -> CompanyProfile:
    p = CompanyProfile(
        name=name, address="1 Main Road, Muscat", phone="+968 2400 0000",
        email="info@alpha.example", website="www.alpha.example", logo_position="right",
        logo_width_mm=42.5, footer_text="Confidential – Alpha", footer_alignment="left",
        show_page_numbers=False, page_number_position="header-center",
        page_number_format="{n} / {total}", accent_color="#1F4E79", pdf_design="formal")
    p = store.set_logo(p, _logo("alpha", (31, 78, 121)))
    return store.save(p)


_FIELDS = ("name", "address", "phone", "email", "website", "logo_position", "logo_width_mm",
           "footer_text", "footer_alignment", "show_page_numbers", "page_number_position",
           "page_number_format", "accent_color", "pdf_design")


def _same(a: CompanyProfile, b: CompanyProfile) -> list[str]:
    return [f for f in _FIELDS if getattr(a, f) != getattr(b, f)]


def test_profiles_import_export() -> None:
    print("\n[T2] company profiles: export -> import on another PC, copy, edit")
    src = ProfileStore(WORK / "pc_a" / "profiles")
    a = _full_profile(src)
    logo_bytes = Path(a.logo_path).read_bytes()
    plain = src.save(CompanyProfile(name="Beta LLC", footer_text="beta"))

    for ext in (".json", ".csv", ".xlsx"):
        out_dir = WORK / f"transfer{ext[1:]}"
        out_dir.mkdir(exist_ok=True)
        exp = out_dir / f"profiles{ext}"
        src.export_file([src.get(a.id), src.get(plain.id)], exp)
        usb = WORK / f"usb{ext[1:]}"                     # carried to another PC:
        shutil.rmtree(usb, ignore_errors=True)
        shutil.copytree(out_dir, usb)
        dst = ProfileStore(WORK / f"pc_b_{ext[1:]}" / "profiles")
        real_logo = Path(a.logo_path)
        hidden = real_logo.with_suffix(".hidden")         # ... where the original path doesn't exist
        real_logo.rename(hidden)
        try:
            res = dst.import_report(usb / exp.name)
        finally:
            hidden.rename(real_logo)
        got = {p.name: p for p in res.imported}
        ia = got.get(a.name)
        diff = _same(a, ia) if ia else ["missing"]
        check(f"{ext}: every field comes across, including the PDF design", not diff
              and len(res.imported) == 2, f"{diff} | {res.summary()}")
        check(f"{ext}: the logo travels with the export (identical image, its own file)",
              ia is not None and ia.logo_path and Path(ia.logo_path).read_bytes() == logo_bytes
              and Path(ia.logo_path).parent == dst.logos and not res.missing_logos,
              res.summary())
        again = dst.import_report(usb / exp.name)
        check(f"{ext}: importing the same file again adds nothing",
              not again.imported and len(again.skipped) == 2 and len(dst.list()) == 2,
              again.summary())
        check(f"{ext}: no half-written or temp files left by the export",
              not [f for f in out_dir.iterdir() if ".tmp" in f.name])

    dst = ProfileStore(WORK / "pc_c" / "profiles")
    mine = dst.save(CompanyProfile(name="Beta LLC", footer_text="my own footer",
                                   accent_color="#006600"))
    before = (dst.dir / f"{mine.id}.json").read_bytes()
    exp = WORK / "transferjson" / "profiles.json"
    res = dst.import_report(exp)
    names = sorted(p.name for p in dst.list())
    check("a same-named but different profile is imported as “Beta LLC (2)” — nothing overwritten",
          "Beta LLC (2)" in names and (dst.dir / f"{mine.id}.json").read_bytes() == before
          and ("Beta LLC", "Beta LLC (2)") in res.renamed, f"{names} | {res.summary()}")

    # a hand-made spreadsheet with loose values and a logo that isn't there
    loose = WORK / "loose.csv"
    loose.write_text(
        "Company Name,Logo,Logo Position,Footer Alignment,Page Number Position,Design,"
        "Logo Width,Page Numbers,Accent\n"
        "Gamma Co,C:\\\\nowhere\\\\gamma.png,Centre,RIGHT,Bottom Left,MODERN,500,no,#ABC\n"
        "Delta Co,,sideways,,top_center,futuristic,abc,,javascript:x\n", encoding="utf-8")
    store = ProfileStore(WORK / "pc_d" / "profiles")
    res = store.import_report(loose)
    g = next((p for p in res.imported if p.name == "Gamma Co"), None)
    d = next((p for p in res.imported if p.name == "Delta Co"), None)
    check("loose spreadsheet values are understood (Centre, RIGHT, Bottom Left, MODERN)",
          g is not None and (g.logo_position, g.footer_alignment, g.page_number_position,
                             g.pdf_design, g.show_page_numbers, g.accent_color)
          == ("center", "right", "footer-left", "modern", False, "#ABC"), str(g))
    check("values that mean nothing fall back to safe defaults (never break an export)",
          d is not None and (d.logo_position, d.footer_alignment, d.page_number_position,
                             d.pdf_design, d.logo_width_mm, d.accent_color)
          == ("left", "center", "header-center", "classic", 35.0, "#8B1E1E")
          and g.logo_width_mm == 90.0, str(d))
    check("a logo that can't be found is reported, and the profile still imports",
          res.missing_logos == ["Gamma Co"] and g.logo_path == "" and "logo not found" in res.summary(),
          res.summary())
    for p in (g, d):
        pages, _ = FP.export(FP.full_minutes(3, 2), f"loose_{p.name[:5]}", p)
        check(f"“{p.name}” (imported with loose values) exports a clean PDF",
              not FP.out_of_bounds(pages) and not FP.overlaps(pages))

    n = len(store.list())
    for bad, text in (("notjson.json", "{oops"), ("list_of_junk.json", '[1, "two", null]'),
                      ("profiles.txt", "name\nX")):
        f = WORK / bad
        f.write_text(text, encoding="utf-8")
        try:
            r = store.import_report(f)
            outcome = f"imported {len(r.imported)}"
        except Exception as exc:
            outcome = f"refused: {exc}"
        check(f"{bad}: nothing imported, existing profiles untouched ({outcome.split(':')[0]})",
              len(store.list()) == n)

    # copy
    c1, c2 = src.duplicate(a.id), src.duplicate(a.id)
    check("Duplicate copies every setting and the PDF design, under “(copy)” / “(copy 2)”",
          not [f for f in _same(a, c1) if f != "name"]
          and (c1.name, c2.name) == (f"{a.name} (copy)", f"{a.name} (copy 2)") and c1.id != a.id)
    check("the copy has its OWN logo file (same image)",
          c1.logo_path != a.logo_path and Path(c1.logo_path).read_bytes() == logo_bytes)
    src.delete(c1.id); src.delete(c2.id)
    check("deleting the copies leaves the original and its logo intact",
          src.get(a.id) is not None and Path(a.logo_path).read_bytes() == logo_bytes)

    # edit (through the real dialog)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from mico360.ui import dialogs as D
    dlg = D.ProfileDialog(src, src.get(a.id))
    _KEEP.append(dlg)
    dlg.footer.setText("Edited footer")
    dlg.reject()
    check("Cancel in the editor changes nothing", not _same(a, src.get(a.id)))
    dlg = D.ProfileDialog(src, src.get(a.id))
    _KEEP.append(dlg)
    check("the editor opens on the company's saved PDF design",
          dlg.design.currentData() == "formal")
    dlg.footer.setText("Edited footer")
    dlg._save()
    saved = src.get(a.id)
    check("Save changes only the edited field; id, logo and PDF design are preserved",
          _same(a, saved) == ["footer_text"] and saved.footer_text == "Edited footer"
          and saved.id == a.id and saved.logo_path == a.logo_path)
    dlg = D.ProfileDialog(src, src.get(a.id))
    _KEEP.append(dlg)
    dlg.design.setCurrentIndex(dlg.design.findData("compact"))
    app.processEvents()
    if D.pdf_preview.available():
        check("the editor previews the REAL PDF for the chosen design", dlg.preview.rendered)
    dlg._save()
    check("choosing a design in the editor saves it with the company",
          src.get(a.id).pdf_design == "compact")
    a2 = src.get(a.id); a2.pdf_design = "formal"; a2.footer_text = a.footer_text; src.save(a2)

    # unreadable profile file
    (src.dir / "broken0001.json").write_text("{not json", encoding="utf-8")
    (src.dir / "list000002.json").write_text("[1, 2]", encoding="utf-8")
    check("an unreadable profile file is skipped, not fatal",
          src.get("broken0001") is None and src.get("list000002") is None
          and {p.id for p in src.list()} == {a.id, plain.id})


# =============================================================================
HEADS = ["Purpose of Meeting", "Meeting Summary", "Key Discussion Points", "Decisions Made",
         "Action Items", "Pending Issues", "Risks or Concerns", "Next Meeting Notes",
         "Closing Summary"]


def _top_strip_colour(path: Path) -> tuple:
    import pypdfium2 as pdfium
    img = pdfium.PdfDocument(str(path))[0].render(scale=1).to_pil().convert("RGB")
    return img.getpixel((img.width // 2, 3))


def test_designs() -> None:
    print("\n[T3] the four PDF designs")
    md = FP.full_minutes()
    texts = {}
    for d in pdf_designs.choices():
        prof = FP.profile(pdf_design=d.key)
        path = P.export_pdf(md, WORK / f"design_{d.key}.pdf", prof)
        pages, meta = FP.read(path)
        texts[d.key] = (path, pages)
        flat_all = " ".join(p.flat for p in pages)
        miss = FP.missing_words(md, pages)
        check(f"{d.name}: every word of the minutes is in the PDF", not miss, str(miss[:8]))
        oob, ov = FP.out_of_bounds(pages), FP.overlaps(pages)
        check(f"{d.name}: nothing outside the page, nothing overlapping", not oob and not ov,
              str((oob + ov)[:3]))
        split = [i for i in range(1, 15) if not any(FP._norm(FP._task(i)) in p.flat for p in pages)]
        tp = [p for p in pages if any(f"workstream {i}," in p.text for i in range(1, 15))]
        check(f"{d.name}: no table row split across pages; the header row repeats",
              not split and len(tp) >= 2 and all("Responsible Person" in p.flat for p in tp),
              f"rows {split}, {len(tp)} table pages")
        stranded = []
        for h in HEADS:
            for p in pages:
                hit = [ln for ln in p.lines if ln[4].lower().endswith(h.lower())]
                if hit and not [ln for ln in p.lines if ln[3] < hit[0][1] - 1 and ln[1] > 17 * MM]:
                    stranded.append(f"{h} (p{p.index + 1})")
        check(f"{d.name}: every section heading present, none stranded at a page bottom",
              not stranded and all(h.lower() in flat_all.lower() for h in HEADS), str(stranded))
        p1 = pages[0]
        check(f"{d.name}: page 1 carries THIS company's logo, name and contact details",
              len(p1.images) == 1 and "MICO360 Technologies LLC" in p1.flat
              and "info@mico360.com" in p1.flat and "Al Khuwair" in p1.flat)
        check(f"{d.name}: the company's footer and “Page n of N” are on every page",
              all("Confidential - for internal use only" in p.flat
                  and f"Page {p.index + 1} of {len(pages)}" in p.flat for p in pages))
        check(f"{d.name}: later pages carry the company and meeting title, no logo",
              all("MICO360 Technologies LLC" in p.flat and "Q4 Programme Steering Committee" in p.flat
                  and not p.images for p in pages[1:]))
        fonts = FP._fonts(path)
        check(f"{d.name}: every font is embedded", fonts and all(e for _n, e in fonts),
              str([n for n, e in fonts if not e]))
        check(f"{d.name}: attendees listed and counted; details shown",
              "Attendees (9)" in p1.flat and all(n in p1.flat for n in FP.PEOPLE)
              and "Board Room 2" in p1.flat and meta.get("Title") == "Q4 Programme Steering Committee")

    c_path, c_pages = texts["classic"]
    m_path, _m = texts["modern"]
    f_path, f_pages = texts["formal"]
    k_path, k_pages = texts["compact"]
    f_flat = " ".join(p.flat for p in f_pages)
    c_flat = " ".join(p.flat for p in c_pages)
    check("Formal: numbered capital headings and a signature block",
          "1. PURPOSE OF MEETING" in f_flat and "Minutes recorded by" in f_flat
          and "Approved by" in f_flat and "Signature" in f_flat)
    check("…which the other designs don't add",
          "Minutes recorded by" not in c_flat and "1. PURPOSE" not in c_flat)
    check("Formal uses the serif family; the others the sans family",
          any("Times" in n or "Georgia" in n or "Cambria" in n for n, _e in FP._fonts(f_path))
          and not any("Times" in n for n, _e in FP._fonts(c_path)), str(FP._fonts(f_path)[:3]))
    r, g, b = _top_strip_colour(m_path)
    check("Modern: the brand-colour strip runs across the top of the page",
          abs(r - 0x8B) < 12 and abs(g - 0x1E) < 12 and abs(b - 0x1E) < 12, str((r, g, b)))
    check("…and Classic has none", _top_strip_colour(c_path) == (255, 255, 255))
    long_md = FP.full_minutes(40, 30)
    n_classic = len(FP.export(long_md, "long_classic", FP.profile(pdf_design="classic"))[0])
    n_compact = len(FP.export(long_md, "long_compact", FP.profile(pdf_design="compact"))[0])
    check("Compact fits long minutes in fewer pages", n_compact < n_classic,
          f"compact {n_compact} vs classic {n_classic}")

    # the hard cases, in every design
    huge = " ".join(f"word{i}" for i in range(1500))
    cases = {
        "a row taller than a page": ("# Meeting Minutes\n**Meeting Title:** Big row\n\n## Action Items\n"
                                     "| Task | Owner | Status |\n| --- | --- | --- |\n"
                                     f"| {huge} | John | Pending |\n", FP.profile),
        "150 attendees": ("# Meeting Minutes\n**Meeting Title:** Town hall\n**Attendees:** "
                          + ", ".join(f"Participant Number {i:03d}" for i in range(1, 151))
                          + "\n\n## Summary\nAll-hands meeting.\n", FP.profile),
        "Arabic minutes": ("# محضر الاجتماع\n**عنوان الاجتماع:** اجتماع اللجنة التوجيهية\n"
                           "**التاريخ:** ٢٩ سبتمبر ٢٠٢٦\n**المكان:** مسقط\n"
                           "**الحضور:** عائشة البلوشية، أحمد الحارثي، فاطمة الرواحي\n\n"
                           "## القرارات\n1. الموافقة على الميزانية\n2. مراجعة العقود\n\n"
                           "## بنود العمل\n| المهمة | المسؤول | الحالة |\n| - | - | - |\n"
                           "| إعداد خطة التسليم | أحمد | قيد التنفيذ |\n",
                           lambda **kw: FP.profile(name="شركة ميكو للتقنية", **kw)),
        "long letterhead and footer": (FP.full_minutes(4, 2), lambda **kw: FP.profile(
            name="The Very Long International Holding Company for Industrial Services and "
                 "Engineering Consultancy (Muscat Branch) LLC",
            address="Building 1234, Way 5678, Block 910, Al Khuwair South, Bawshar, Muscat "
                    "Governorate, Sultanate of Oman, P.O. Box 1234, Postal Code 112",
            footer_text="Confidential and proprietary. " * 12, logo_position="center", **kw)),
        "names in many scripts": ("# Meeting Minutes\n**Meeting Title:** Global sync\n"
                                  "**Attendees:** Łukasz Dvořák, Ольга Петрова, 张伟, Nguyễn Văn An\n\n"
                                  "## Notes\nBudget ₹ 5,00,000 approved ✅. *Italic* and `code`.\n",
                                  FP.profile),
    }
    for label, (text, make) in cases.items():
        bad = []
        for d in pdf_designs.choices():
            try:
                pages, _ = FP.export(text, f"hard_{d.key}_{label[:6]}", make(pdf_design=d.key))
                problems = FP.out_of_bounds(pages) + FP.overlaps(pages)
                if "Arabic" not in label:
                    problems += [f"missing {w}" for w in FP.missing_words(text, pages)[:3]]
                if "\u25a0" in " ".join(p.text for p in pages):
                    problems.append("black box")
                if problems:
                    bad.append(f"{d.key}: {problems[:2]}")
            except Exception as exc:
                bad.append(f"{d.key}: {exc!r}")
        check(f"{label}: clean in all four designs", not bad, str(bad[:3]))

    # the design belongs to the company
    store = ProfileStore(WORK / "designs" / "profiles")
    a = store.save(FP.profile(name="Alpha Co", footer_text="Alpha footer", pdf_design="modern"))
    b = store.save(FP.profile(name="Beta Co", footer_text="Beta footer", accent_color="#1F4E79",
                              pdf_design="formal"))
    pa = service.export(md, WORK / "co_a.pdf", store.get(a.id))
    pb = service.export(md, WORK / "co_b.pdf", store.get(b.id))
    ta = " ".join(p.flat for p in FP.read(pa)[0])
    tb = " ".join(p.flat for p in FP.read(pb)[0])
    check("each company's export uses ITS design, header and footer",
          "Alpha Co" in ta and "Alpha footer" in ta and "Minutes recorded by" not in ta
          and "Beta" not in ta and "Beta Co" in tb and "Beta footer" in tb
          and "Minutes recorded by" in tb and "Alpha" not in tb)
    ar, ag, ab = _top_strip_colour(pa)
    check("…and its own brand colour", abs(ar - 0x8B) < 12 and abs(ab - 0x1E) < 12, str((ar, ag, ab)))
    x = store.get(a.id); x.pdf_design = "compact"; store.save(x)
    reopened = ProfileStore(store.dir)
    check("changing one company's design leaves the other's untouched, and both persist",
          reopened.get(a.id).pdf_design == "compact" and reopened.get(b.id).pdf_design == "formal")
    raw = json.loads((store.dir / f"{b.id}.json").read_text(encoding="utf-8"))
    raw["pdf_design"] = "hologram"
    raw.pop("accent_color")
    (store.dir / f"{b.id}.json").write_text(json.dumps(raw), encoding="utf-8")
    old = store.get(b.id)
    check("a profile from another version (unknown design, missing fields) still loads and exports",
          old is not None and pdf_designs.get(old.pdf_design).key == "classic"
          and Path(service.export(md, WORK / "old.pdf", old)).stat().st_size > 2000)
    legacy = {k: v for k, v in raw.items() if k != "pdf_design"}
    check("a profile saved before designs existed uses Classic",
          CompanyProfile.from_dict(legacy).pdf_design == "classic")
    plain = service.export(md, WORK / "plain_formal.pdf", None, design="formal")
    check("an export without a company can still be given a design",
          "Minutes recorded by" in " ".join(p.flat for p in FP.read(plain)[0]))

    # two exports at once (Export button + e-mail attachment) keep their own design
    made: dict = {}

    def run(key: str) -> None:
        for n in range(4):
            made[(key, n)] = P.export_pdf(md, WORK / f"par_{key}_{n}.pdf",
                                          FP.profile(pdf_design=key))
    threads = [threading.Thread(target=run, args=(k,)) for k in ("formal", "classic", "compact")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # read back one at a time, on this thread (the PDF reader isn't thread-safe)
    out = {k: " ".join(p.flat for p in FP.read(path)[0]) for k, path in made.items()}
    check("exports running at the same time never mix their designs",
          all(("Minutes recorded by" in v) == (k[0] == "formal") for k, v in out.items())
          and len(out) == 12)


# =============================================================================
def test_app_workflow() -> None:
    print("\n[T4] the app: design per company, branding on every export")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QThread, Signal
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    app = QApplication.instance() or QApplication([])
    from mico360.core import ollama_client as OC
    OC.check_status = lambda *a, **k: OC.OllamaStatus(running=False, models=[], error="test")
    from mico360.core.history import Meeting
    from mico360.ui import dialogs as D
    from mico360.ui import pages as PG
    from mico360.ui import pdf_preview
    from mico360.ui import workers as W
    from mico360.ui.context import AppContext
    from mico360.ui.pdf_design_tab import PdfDesignTab
    from mico360.ui.profiles_page import ProfilesPage
    from mico360.ui.prompts_page import PromptsPage

    def wait_for(cond, timeout=25.0):
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
            self.calls.append(text)

    ctx = AppContext()
    for p in ctx.profiles.list():                        # a clean slate for this test
        ctx.profiles.delete(p.id)
    ctx.settings.set("active_profile", "")
    toast = Toast()
    _KEEP.extend([ctx, toast])
    infos: list = []
    orig_info, orig_q = QMessageBox.information, QMessageBox.question
    QMessageBox.information = staticmethod(lambda *a, **k: infos.append(a[2] if len(a) > 2 else ""))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

    # -- Company Profiles page --------------------------------------------------
    ppage = ProfilesPage(ctx, toast)
    _KEEP.append(ppage)
    a = ctx.profiles.save(FP.profile(name="Alpha Co", footer_text="Alpha footer"))
    ppage.reload()
    check("the first company becomes the active one automatically",
          ctx.settings.get("active_profile") == a.id)
    b = ctx.profiles.save(FP.profile(name="Beta Co", footer_text="Beta footer",
                                     accent_color="#1F4E79", pdf_design="formal"))
    ppage.reload()
    check("adding a second company doesn't change the active one",
          ctx.settings.get("active_profile") == a.id)
    ppage._duplicate(b.id)
    copy = next((p for p in ctx.profiles.list() if p.name == "Beta Co (copy)"), None)
    check("Duplicate on a profile card makes a full copy",
          copy is not None and copy.pdf_design == "formal" and copy.footer_text == "Beta footer")
    exp = WORK / "ui_profiles_export"
    orig_save, orig_open = QFileDialog.getSaveFileName, QFileDialog.getOpenFileName
    QFileDialog.getSaveFileName = staticmethod(
        lambda *a_, **k: (str(exp), "JSON — logos included (*.json)"))
    try:
        ppage._export()
    finally:
        QFileDialog.getSaveFileName = orig_save
    check("Export without typing an extension still writes a .json file",
          (WORK / "ui_profiles_export.json").exists())
    QFileDialog.getOpenFileName = staticmethod(lambda *a_, **k: (str(exp) + ".json", ""))
    try:
        n = len(ctx.profiles.list())
        ppage._import()
    finally:
        QFileDialog.getOpenFileName = orig_open
    check("re-importing the export adds no duplicates and says so",
          len(ctx.profiles.list()) == n and infos and "skipped 3" in infos[-1], str(infos[-1:]))
    ctx.profiles.delete(copy.id)

    # -- Settings -> PDF design ---------------------------------------------------
    tab = PdfDesignTab(ctx, toast)
    _KEEP.append(tab)
    tab.refresh()
    check("Settings → PDF design opens on the active company with its saved design marked",
          tab.company_id() == a.id and tab.current_design() == "classic"
          and not tab._cards["classic"]["btn"].isEnabled())
    if pdf_preview.available():
        thumbs = [tab._cards[d.key]["thumb"].pixmap() for d in pdf_designs.choices()]
        check("each design shows a real rendered preview",
              all(t is not None and not t.isNull() for t in thumbs))
        img = pdf_preview.render_design(ctx.profiles.get(a.id), "modern", 300)
        dark = sum(1 for x in range(0, img.width(), 7) for y in range(0, img.height(), 7)
                   if img.pixelColor(x, y).lightness() < 200)
        check("…page-shaped and not blank",
              img is not None and 1.38 < img.height() / img.width() < 1.45 and dark > 50, str(dark))
    else:
        check("Qt's PDF module is available for previews", False)
    tab.select("modern")
    check("choosing a design saves it for THAT company only",
          ctx.profiles.get(a.id).pdf_design == "modern" and ctx.profiles.get(b.id).pdf_design == "formal"
          and not tab._cards["modern"]["btn"].isEnabled())
    tab.company.setCurrentIndex(tab.company.findData(b.id)); tab.refresh(force=True)
    check("switching company shows that company's own design", tab.current_design() == "formal")
    tab.select("compact")
    check("…and saving it doesn't touch the first company",
          ctx.profiles.get(b.id).pdf_design == "compact" and ctx.profiles.get(a.id).pdf_design == "modern")
    tab.company.setCurrentIndex(tab.company.findData("")); tab.refresh(force=True)
    tab.select("formal")
    check("exports without a company keep their own design in the settings",
          ctx.settings.get("pdf_design") == "formal"
          and ctx.profiles.get(a.id).pdf_design == "modern")
    tab.company.setCurrentIndex(tab.company.findData(b.id)); tab.refresh(force=True)
    tab.select("formal")
    ctx2 = AppContext()
    _KEEP.append(ctx2)
    check("the choices survive a restart",
          ctx2.profiles.get(a.id).pdf_design == "modern" and ctx2.profiles.get(b.id).pdf_design == "formal"
          and ctx2.settings.get("pdf_design") == "formal")

    # -- Minutes step: branding ---------------------------------------------------
    page = PG.NewMeetingPage(ctx, toast)
    _KEEP.append(page)
    page.minutes.setPlainText(FP.full_minutes(3, 2))
    page.meeting_title.setText("Branding check")
    page.refresh_brands()
    check("a new meeting is branded for the active company",
          page.brand_box.currentData() == a.id and page._branding_profile().name == "Alpha Co")
    out_dir = WORK / "brand_exports"
    out_dir.mkdir(exist_ok=True)
    counter = [0]

    def fake_save(parent, caption, start, filters, selected=""):
        counter[0] += 1
        return str(out_dir / f"export{counter[0]}"), "PDF Document (*.pdf)"
    PG.QFileDialog.getSaveFileName = staticmethod(fake_save)

    def export_text() -> str:
        page._export()
        wait_for(lambda: page.export_btn.isEnabled())
        pdf = out_dir / f"export{counter[0]}.pdf"
        return " ".join(p.flat for p in FP.read(pdf)[0]) if pdf.exists() else ""

    try:
        t1 = export_text()
        check("its PDF has that company's header, footer and design (Modern: no signature block)",
              "Alpha Co" in t1 and "Alpha footer" in t1 and "Beta" not in t1
              and "Minutes recorded by" not in t1)
        page.brand_box.setCurrentIndex(page.brand_box.findData(b.id))
        page._brand_chosen()
        t2 = export_text()
        check("choosing another company on the Minutes step brands the export for it "
              "(header, footer, Formal design)",
              "Beta Co" in t2 and "Beta footer" in t2 and "Alpha" not in t2
              and "Minutes recorded by" in t2)
        check("…without changing the active company", ctx.settings.get("active_profile") == a.id)
        mid = page._save_history(silent=True)
        check("the meeting remembers its company", ctx.history.get(mid).profile_id == b.id)

        page._new_meeting(quiet=True)
        check("the next new meeting is back on the active company",
              page.brand_box.currentData() == a.id)
        page.load_meeting(ctx.history.get(mid))
        check("opening the saved meeting restores ITS company, and says the active one differs",
              page.brand_box.currentData() == b.id and "Alpha Co" in page.brand_hint.text())
        t3 = export_text()
        check("its export is branded for its own company, not the active one",
              "Beta Co" in t3 and "Alpha" not in t3)

        page.brand_box.setCurrentIndex(page.brand_box.findData(""))
        page._brand_chosen()
        t4 = export_text()
        check("“No company branding” exports with no letterhead, in the design chosen for "
              "plain exports", "Alpha Co" not in t4 and "Beta Co" not in t4
              and "Minutes recorded by" in t4)
        page._new_meeting(quiet=True)
        page.load_meeting(ctx.history.get(mid))
        check("that choice is kept with the meeting (reopening it doesn't re-brand it)",
              page.brand_box.currentData() == "" and page._branding_profile() is None)
        page.brand_box.setCurrentIndex(page.brand_box.findData(b.id))
        page._brand_chosen()                              # back to Beta for the checks below

        gone = ctx.history.save(Meeting(id=0, title="Orphan", created_at=0, updated_at=0,
                                        source_type="text", transcript="t",
                                        minutes=FP.full_minutes(2, 1), profile_id="deleted999"))
        page.load_meeting(ctx.history.get(gone))
        t5 = export_text()
        check("a meeting whose company was deleted falls back to the active company",
              page.brand_box.currentData() == a.id and "Alpha Co" in t5)

        ap = ctx.profiles.get(a.id); ap.footer_text = "Alpha footer v2"; ap.pdf_design = "formal"
        ctx.profiles.save(ap)
        t6 = export_text()
        check("editing the company (footer, design) shows up in the very next export",
              "Alpha footer v2" in t6 and "Minutes recorded by" in t6)
        (ctx.profiles.dir / f"{a.id}.json").write_text("{broken", encoding="utf-8")
        t7 = export_text()
        check("a damaged company profile doesn't break exporting (plain export instead)",
              bool(t7) and "Alpha Co" not in t7, t7[:60])
        ctx.profiles.save(ap)
    finally:
        PG.QFileDialog.getSaveFileName = orig_save

    # e-mail: same company as the export
    class FakeEmail(QThread):
        finished_ok = Signal()
        failed = Signal(str)
        made = []

        def __init__(self, cfg, to, subject, body, html=None, attachments=None, cc=None):
            super().__init__()
            self.attachments, self.html, self.text = list(attachments or []), html or "", ""
            FakeEmail.made.append(self)

        def run(self):
            self.text = " ".join(p.flat for a_ in self.attachments for p in FP.read(Path(a_))[0])
            self.finished_ok.emit()
    for k, v in (("smtp_host", "smtp.example.com"), ("smtp_user", "u"), ("smtp_password", "p"),
                 ("email_from", "me@example.com")):
        ctx.settings.set(k, v)
    page.load_meeting(ctx.history.get(mid))               # the Beta meeting
    orig_exec, orig_worker = D.EmailComposeDialog.exec, W.EmailWorker

    def fake_exec(dlg):
        dlg.to.setText("someone@example.com"); dlg.att_pdf.setChecked(True)
        dlg.att_docx.setChecked(False)
        return 1
    D.EmailComposeDialog.exec, W.EmailWorker = fake_exec, FakeEmail
    try:
        page._email_minutes()
        wait_for(lambda: FakeEmail.made and not FakeEmail.made[-1].isRunning()
                 and page.email_btn.isEnabled())
    finally:
        D.EmailComposeDialog.exec, W.EmailWorker = orig_exec, orig_worker
    em = FakeEmail.made[-1] if FakeEmail.made else None
    check("the e-mailed PDF and the e-mail body carry the meeting's company branding",
          em is not None and "Beta Co" in em.text and "Beta footer" in em.text
          and "Alpha" not in em.text and "Beta Co" in em.html, em.text[:80] if em else "")

    # -- Prompt Library page ---------------------------------------------------------
    prompts = PromptsPage(ctx, toast)
    _KEEP.append(prompts)
    mine = ctx.prompts.add("UI template", TRICKY, category="Mine")
    prompts.reload()
    pexp = WORK / "ui_prompts"
    orig_box_exec = QMessageBox.exec

    def pick_mine(box):
        btn = next((x for x in box.buttons() if x.text().startswith("My prompts")), None)
        box.clickedButton = lambda: btn
        return 0
    QMessageBox.exec = pick_mine
    QFileDialog.getSaveFileName = staticmethod(lambda *a_, **k: (str(pexp), "Prompts (*.json)"))
    try:
        prompts._export()
    finally:
        QMessageBox.exec = orig_box_exec
        QFileDialog.getSaveFileName = orig_save
    exported = json.loads((WORK / "ui_prompts.json").read_text(encoding="utf-8"))["prompts"]
    check("Prompt Library → Export writes your own prompts (not the untouched built-ins)",
          [r["name"] for r in exported] == ["UI template"] and exported[0]["text"] == TRICKY)
    ctx.prompts.delete(mine.id)
    QFileDialog.getOpenFileName = staticmethod(lambda *a_, **k: (str(pexp) + ".json", ""))
    try:
        prompts._import()
    finally:
        QFileDialog.getOpenFileName = orig_open
    back = next((p for p in ctx.prompts.list() if p.name == "UI template"), None)
    check("Prompt Library → Import brings it back exactly and selects it",
          back is not None and back.text == TRICKY and prompts._current() is not None
          and prompts._current().id == back.id)
    rich = ("A B non-breaking\n\ttab\ttab  \ntrailing   \n\nzero​width  separator\n"
            "emoji ✅ \U0001F389\n[TRANSCRIPT_HERE]\n\n")
    dlg = D.PromptDialog("Rich", rich, "Mine")
    _KEEP.append(dlg)
    check("opening a prompt in the editor and saving changes nothing (non-breaking spaces, "
          "tabs, blank lines, separators kept)", dlg.values() == ("Rich", rich, "Mine"),
          repr(dlg.values()[1][:30]))
    fm = next(p for p in ctx.prompts.list() if p.name == "Formal Minutes")
    ctx.prompts.update(fm.id, fm.name, fm.text + "\nmy tweak")
    prompts.reload(); prompts._select(fm.id); app.processEvents()
    check("an edited built-in shows “Restore default”",
          not prompts.restore_btn.isHidden() and "edited" in prompts.pv_tag.text())
    prompts._restore()
    check("…which puts the original text back",
          ctx.prompts.get(fm.id).text == fm.text and prompts.restore_btn.isHidden())
    QMessageBox.information, QMessageBox.question = orig_info, orig_q


# =============================================================================
def main() -> int:
    for fn in (test_prompt_templates, test_profiles_import_export, test_designs,
               test_app_workflow):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} ran without errors", False, repr(exc))
    passed = sum(ok for _, ok, _ in results)
    print(f"\n==== TEMPLATES: {passed}/{len(results)} passed ====")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        shutil.rmtree(FP._TMP, ignore_errors=True)
        from mico360.hard_exit import hard_exit
        hard_exit(rc)
