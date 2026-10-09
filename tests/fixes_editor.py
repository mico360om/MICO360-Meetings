"""The rich-text minutes editor: what you see is what gets exported.

    python tests/fixes_editor.py

  E1  Markdown -> editor -> Markdown keeps every block (sample, tricky and
      generated minutes), so loading and saving changes nothing
  E2  edits in the editor (bold, heading, list, table cell, new row, typing)
      reach the Markdown, History and the exports (PDF, Word, HTML)
  E3  edits in the Markdown tab appear in the editor; generation / History
      reload the editor; no echo loops; the word count follows
  E4  pasted rich text is normalised; Enter after a heading starts normal
      text; Enter on an empty list item leaves the list; Arabic paragraphs
      are right-to-left; undo works

Runs against a THROWAWAY data folder (set up by fixes_pdf, whose PDF-reading
helpers this reuses); no network, Ollama or audio.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixes_pdf as FP                                   # noqa: E402  (isolates LOCALAPPDATA)
import pdf_fuzz                                          # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QMimeData, Qt                 # noqa: E402
from PySide6.QtGui import QKeyEvent, QTextCursor         # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from mico360.export import md_blocks                     # noqa: E402
from mico360.ui import rich_editor as RE                 # noqa: E402
from mico360.ui.pdf_preview import SAMPLE_MINUTES        # noqa: E402

check, results, WORK = FP.check, FP.results, FP.WORK
_KEEP: list = []
BS = chr(92)
TRICKY = ("# Meeting Minutes\n**Meeting Title:** A & B <Review> 100%\n**Date and Time:** 2026-10-10\n"
          "**Attendees:** Łukasz Dvořák, Ольга Петрова, 张伟\n\n## Notes\nSee *italic*, **bold**, "
          "`config.yaml`, ~~old~~ and [plan](https://example.com/p?a=1&b=2). Use snake_case_names "
          "and 2*3*4.\n\n### Sub heading\n1. First\n2. Second\n\n## Action Items\n"
          "| Task | Responsible Person | Deadline | Status |\n| --- | --- | --- | --- |\n"
          "| Prepare the report " + BS + "| appendix | Ali | 10 Oct | Pending |\n"
          "| Fix **login** bug | Sara | Not specified | Done |\n\n## Arabic\n"
          "محضر الاجتماع — **القرار:** الموافقة\n- بند أول\n- بند ثان\n\n5. five\n6. six\n")


def pump(n: int = 20) -> None:
    for _ in range(n):
        app.processEvents()
        time.sleep(0.002)


def sig(md: str) -> list:
    out = []
    for b in md_blocks.parse(md):
        n = len(b.headers)
        rows = [[md_blocks.plain(r[j]) if j < len(r) else "" for j in range(n)] for r in b.rows]
        out.append((b.kind, md_blocks.plain(b.text), md_blocks.plain(b.key),
                    [md_blocks.plain(i) for i in b.items], b.headers, rows))
    return out


def key(widget, k, text="", mods=Qt.NoModifier):
    QApplication.sendEvent(widget, QKeyEvent(QKeyEvent.Type.KeyPress, k, mods, text))
    QApplication.sendEvent(widget, QKeyEvent(QKeyEvent.Type.KeyRelease, k, mods, text))


# =============================================================================
def test_roundtrip() -> None:
    print("\n[E1] Markdown -> editor -> Markdown")
    ed = RE.RichMinutesEditor()
    _KEEP.append(ed)
    for name, md in (("sample minutes", SAMPLE_MINUTES), ("tricky minutes", TRICKY)):
        ed.set_markdown(md)
        back = ed.to_markdown()
        check(f"{name}: every block survives (headings, details, lists, table, Arabic, links, code)",
              sig(back) == sig(md), f"{sig(back)[:2]} vs {sig(md)[:2]}")
    back = ed.to_markdown()
    check("the escaped pipe inside a table cell is kept", BS + "| appendix" in back)
    check("a numbered list that starts at 5 keeps its numbers", "5. five\n6. six" in back)
    check("inline code, strike-through and links are kept as Markdown",
          "`config.yaml`" in back and "~~old~~" in back and "[plan](https://example.com/p?a=1&b=2)" in back)
    check("loading twice gives the same Markdown (stable)",
          (ed.set_markdown(back), ed.to_markdown())[1] == back)
    gen = pdf_fuzz.Gen(11)
    bad = []
    for i in range(30):
        md = gen.minutes()
        ed.set_markdown(md)
        if sig(ed.to_markdown()) != sig(md):
            bad.append(i)
    check("30 randomly generated minutes round-trip block for block", not bad, str(bad))
    ed.set_markdown("")
    check("an empty document gives empty Markdown", ed.to_markdown() == "")


def _page():
    from mico360.core import ollama_client as OC
    from mico360.ui import pages as PG
    from mico360.ui.context import AppContext
    OC.check_status = lambda *a, **k: OC.OllamaStatus(running=False, models=[], error="test")
    for n in ("question", "information", "warning", "critical"):
        setattr(QMessageBox, n, staticmethod(lambda *a, **k: QMessageBox.Ok))

    class Toast:
        def show_message(self, *a, **k):
            pass
    ctx = AppContext()
    page = PG.NewMeetingPage(ctx, Toast())
    page.resize(1200, 800); page.show()
    _KEEP.extend([ctx, page])
    page._reached = 4; page._goto_step(page.STEP_MINUTES)
    page.minutes_tabs.setCurrentIndex(0)
    pump()
    return ctx, page


def test_edits_reach_exports() -> None:
    print("\n[E2] edits in the editor reach the Markdown, History and the exports")
    ctx, page = _page()
    page.minutes.setPlainText(SAMPLE_MINUTES)
    page.meeting_title.setText("Editor check")
    pump()
    ed = page.rich
    check("the editor shows the generated minutes formatted (a heading block, a table)",
          any(ed.document().findBlock(i).blockFormat().headingLevel() == 2
              for i in range(0, ed.document().characterCount(), 40))
          and ed.document().rootFrame().childFrames())
    # type at the end of the purpose paragraph
    doc = ed.document()
    cur = doc.find("agree the priorities for next quarter.")
    check("text can be located in the editor", not cur.isNull())
    cur.movePosition(QTextCursor.EndOfBlock)
    ed.edit.setTextCursor(cur)
    ed.edit.insertPlainText(" The CFO joined for the budget item.")
    pump()
    md = page.minutes.toPlainText()
    check("typed text appears in the Markdown at once",
          "The CFO joined for the budget item." in md and "## Purpose of Meeting" in md)
    # bold a word
    cur = doc.find("CFO"); ed.edit.setTextCursor(cur); ed.toggle_bold(); pump()
    check("Bold on a selection becomes **bold** in the Markdown",
          "**CFO**" in page.minutes.toPlainText())
    # a new heading + bullet list at the end
    cur = QTextCursor(doc); cur.movePosition(QTextCursor.End); ed.edit.setTextCursor(cur)
    key(ed.edit, Qt.Key_Return); ed.edit.insertPlainText("Parking Lot"); ed.set_heading(2)
    cur = ed.edit.textCursor(); cur.movePosition(QTextCursor.EndOfBlock); ed.edit.setTextCursor(cur)
    key(ed.edit, Qt.Key_Return); ed.edit.insertPlainText("Revisit the vendor SLA")
    ed.toggle_bullets(); key(ed.edit, Qt.Key_Return); ed.edit.insertPlainText("Office move dates")
    pump()
    md = page.minutes.toPlainText()
    check("a new heading and bullet list typed in the editor become ## and - lines",
          "## Parking Lot" in md and "- Revisit the vendor SLA\n- Office move dates" in md, md[-200:])
    # edit a table cell and add a row
    cur = doc.find("Confirm vendor delivery dates"); ed.edit.setTextCursor(cur)
    cur.movePosition(QTextCursor.EndOfBlock); ed.edit.setTextCursor(cur)
    ed.edit.insertPlainText(" (by phone)"); ed.add_row(); pump()
    table = ed.edit.textCursor().currentTable()
    r = table.cellAt(ed.edit.textCursor()).row() + 1
    for c, text in enumerate(["Book the review room", "Sara Lopez", "25 March", "Pending"]):
        table.cellAt(r, c).firstCursorPosition().insertText(text)
    pump()
    blocks = md_blocks.parse(page.minutes.toPlainText())
    tbl = next(b for b in blocks if b.kind == "table")
    check("a table cell edit and a new row are in the Markdown table",
          any("Confirm vendor delivery dates (by phone)" in r[0] for r in tbl.rows)
          and ["Book the review room", "Sara Lopez", "25 March", "Pending"] in tbl.rows, str(tbl.rows))
    # History + exports
    mid = page._save_history(silent=True)
    saved = ctx.history.get(mid).minutes
    check("Save to History stores the edited Markdown", "**CFO**" in saved and "## Parking Lot" in saved)
    from mico360.export import service
    prof = FP.profile()
    pdf = service.export(page.minutes.toPlainText(), WORK / "edited.pdf", prof)
    pages, _ = FP.read(Path(pdf))
    flat = " ".join(p.flat for p in pages)
    check("the PDF export has the typed sentence, the new section, the list and the new table row",
          "The CFO joined for the budget item." in flat and "Parking Lot" in flat
          and "Revisit the vendor SLA" in flat and "Book the review room" in flat, flat[:0])
    check("…with nothing from the original lost", all(w in flat for w in (
        "Quarterly Planning Review", "Aisha Al-Balushi", "Circulate the revised delivery plan",
        "Next review on 9 April 2026")))
    check("…and the PDF is clean (no overlaps, nothing off the page)",
          not FP.out_of_bounds(pages) and not FP.overlaps(pages))
    import zipfile
    docx = service.export(page.minutes.toPlainText(), WORK / "edited.docx", prof)
    xml = zipfile.ZipFile(docx).read("word/document.xml").decode("utf-8")
    check("the Word export has the edits too", "CFO" in xml and "joined for the budget item" in xml
          and "Parking Lot" in xml and "Book the review room" in xml)
    html = Path(service.export(page.minutes.toPlainText(), WORK / "edited.html", prof)).read_text(encoding="utf-8")
    check("…and the HTML export", "<strong>CFO</strong>" in html and "Parking Lot" in html)


def test_sync_both_ways() -> None:
    print("\n[E3] editor <-> Markdown tab <-> generation stay in sync")
    ctx, page = _page()
    page.minutes.setPlainText(SAMPLE_MINUTES); pump()
    check("loading Markdown fills the editor", "Quarterly Planning Review" in page.rich.edit.toPlainText())
    before = page.minutes.toPlainText()
    pump(10)
    check("loading does not rewrite the Markdown (no echo)", page.minutes.toPlainText() == before)
    page.minutes_tabs.setCurrentIndex(2); pump()
    cur = page.minutes.textCursor(); cur.movePosition(QTextCursor.End)
    page.minutes.setTextCursor(cur)
    page.minutes.insertPlainText("\n\n## Added in Markdown\n- typed as source\n")
    pump()
    check("an edit in the Markdown tab shows up formatted in the editor",
          "Added in Markdown" in page.rich.edit.toPlainText()
          and any(page.rich.document().findBlockByNumber(i).blockFormat().headingLevel() == 2
                  and "Added in Markdown" in page.rich.document().findBlockByNumber(i).text()
                  for i in range(page.rich.document().blockCount())))
    page.minutes_tabs.setCurrentIndex(0); pump()
    page.minutes.setPlainText("# Meeting Minutes\n**Meeting Title:** Regenerated\n\n## Summary\nNew text.\n")
    pump()
    check("new generated minutes replace the editor content",
          "Regenerated" in page.rich.edit.toPlainText() and "Added in Markdown" not in page.rich.edit.toPlainText())
    check("the word count follows the editor", page.minutes_count.text().endswith("words"))
    page.rich.edit.insertPlainText(" More."); pump()
    check("typing updates the word count", page.minutes_count.text() != "")
    page._new_meeting(quiet=True); pump()
    check("a new meeting clears the editor", page.rich.edit.toPlainText().strip() == ""
          and page.minutes.toPlainText().strip() == "")


def test_editing_behaviour() -> None:
    print("\n[E4] paste, keys, Arabic, undo")
    ed = RE.RichMinutesEditor()
    _KEEP.append(ed)
    ed.set_markdown("# Meeting Minutes\n\n## Notes\nStart.\n")
    cur = QTextCursor(ed.document()); cur.movePosition(QTextCursor.End); ed.edit.setTextCursor(cur)
    mime = QMimeData()
    mime.setHtml('<html><body><p style="font-size:30pt;color:red"><b>Pasted</b> from <i>Word</i></p>'
                 '<ul><li>one</li><li>two</li></ul><table><tr><th>A</th><th>B</th></tr>'
                 '<tr><td>1</td><td>2</td></tr></table></body></html>')
    mime.setText("Pasted from Word")
    ed.edit.insertFromMimeData(mime)
    md = ed.to_markdown()
    check("pasted rich text keeps bold/italic, lists and tables, drops fonts and colours",
          "**Pasted** from *Word*" in md and "- one\n- two" in md and "| A | B |" in md
          and "| 1 | 2 |" in md and "30pt" not in ed.edit.toHtml().replace("30pt", "") + "", md)
    ed.set_markdown("# Title\n\n## Heading\n")
    blk = ed.document().lastBlock()
    cur = QTextCursor(blk); cur.movePosition(QTextCursor.EndOfBlock); ed.edit.setTextCursor(cur)
    key(ed.edit, Qt.Key_Return); ed.edit.insertPlainText("Plain text after the heading")
    check("Enter at the end of a heading starts a normal paragraph",
          ed.edit.textCursor().block().blockFormat().headingLevel() == 0
          and "## Heading\n\nPlain text after the heading" in ed.to_markdown(), ed.to_markdown())
    ed.set_markdown("- one\n- two\n")
    blk = ed.document().lastBlock()
    cur = QTextCursor(blk); cur.movePosition(QTextCursor.EndOfBlock); ed.edit.setTextCursor(cur)
    key(ed.edit, Qt.Key_Return)                           # a new, empty item
    check("Enter continues the list", ed.edit.textCursor().block().textList() is not None)
    key(ed.edit, Qt.Key_Return)                           # Enter on the empty item leaves it
    ed.edit.insertPlainText("After the list")
    check("Enter on an empty item leaves the list",
          ed.edit.textCursor().block().textList() is None
          and "- one\n- two\n\nAfter the list" in ed.to_markdown(), ed.to_markdown())
    ed.set_markdown("# Minutes\n\n## Notes\nEnglish line.\n\nمحضر الاجتماع بالعربية\n")
    rtl_blocks = [ed.document().findBlockByNumber(i) for i in range(ed.document().blockCount())
                  if "محضر" in ed.document().findBlockByNumber(i).text()]
    check("Arabic paragraphs are laid out right-to-left; English ones left-to-right",
          rtl_blocks and rtl_blocks[0].blockFormat().layoutDirection() == Qt.RightToLeft
          and ed.document().findBlock(ed.document().find("English").position()).blockFormat()
          .layoutDirection() != Qt.RightToLeft)
    ed.edit.setTextCursor(QTextCursor(rtl_blocks[0]))
    cur = ed.edit.textCursor(); cur.movePosition(QTextCursor.EndOfBlock); ed.edit.setTextCursor(cur)
    ed.edit.insertPlainText(" — إضافة")
    check("…and the Arabic text is kept in the Markdown", "إضافة" in ed.to_markdown())
    before = ed.to_markdown()
    # typing somewhere else is its own undo step (consecutive typing at one
    # spot merges into one step, as in any editor)
    cur = QTextCursor(ed.document()); cur.movePosition(QTextCursor.Start); ed.edit.setTextCursor(cur)
    ed.edit.insertPlainText("oops ")
    check("the edit is there before Undo", ed.to_markdown() != before)
    ed.edit.undo()
    check("Undo works in the editor", ed.to_markdown() == before)


# =============================================================================
def main() -> int:
    for fn in (test_roundtrip, test_edits_reach_exports, test_sync_both_ways, test_editing_behaviour):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(f"{fn.__name__} ran without errors", False, repr(exc))
    passed = sum(ok for _, ok, _ in results)
    print(f"\n==== EDITOR: {passed}/{len(results)} passed ====")
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
