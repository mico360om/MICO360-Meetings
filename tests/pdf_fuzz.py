"""Randomised minutes x random company profiles x every PDF design.

    python tests/pdf_fuzz.py [seed] [documents]      (default: 7, 60)

Each generated document is exported in all four designs and read back: no
crash, nothing outside the printable area, no overlapping text, no black
boxes, and every word of the minutes present (line and page breaks ignored).
tests/fixes_templates.py runs a short fixed-seed pass of this.
"""
from __future__ import annotations

import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixes_pdf as FP                                   # noqa: E402  (isolates LOCALAPPDATA)

from mico360.export import md_blocks, pdf_designs        # noqa: E402
from mico360.export import pdf_export as P               # noqa: E402

WORDS = ("budget review vendor timeline migration security rollout approval dependency "
         "infrastructure Q4 plan risk owner deadline status contract renewal staffing").split()
LONG = ["Supercalifragilisticexpialidocious" * 3, "https://example.com/" + "path/" * 30,
        "A" * 120, "x_y_z" * 25]
NAMES = ["Aisha Al-Balushi", "John Smith", "Łukasz Dvořák", "Ольга Петрова", "张伟",
         "Fatima Al-Harthy (Chair)", "Dr. A. B. C. D'Souza-Montgomery III", "Bo"]
HEADS = ["Task", "Responsible Person", "Deadline", "Status", "Notes", "Priority", "Area",
         "Ref", "Cost", "Risk", "Phase", "Team", "#"]


class Gen:
    def __init__(self, seed: int):
        self.r = random.Random(seed)

    def sent(self, n: int | None = None) -> str:
        r = self.r
        w = [r.choice(WORDS) for _ in range(n or r.randint(3, 30))]
        if r.random() < 0.12:
            w.insert(r.randrange(len(w)), r.choice(LONG))
        if r.random() < 0.2:
            i = r.randrange(len(w)); w[i] = f"**{w[i]}**"
        if r.random() < 0.1:
            i = r.randrange(len(w)); w[i] = f"*{w[i]}*"
        return " ".join(w).capitalize() + "."

    def table(self) -> str:
        r = self.r
        cols = r.choice([1, 2, 3, 4, 4, 5, 8, 12])
        heads = r.sample(HEADS, cols)
        if r.random() < 0.1:
            heads[r.randrange(cols)] = ""
        rows = []
        for _ in range(r.choice([0, 1, 2, 5, 12, 40])):
            row = []
            for h in heads:
                if h == "Status":
                    row.append(r.choice(["Pending", "Done", "In Progress", "Blocked",
                                         "Not specified", ""]))
                elif h in ("Task", "Notes"):
                    row.append(self.sent(r.choice([2, 8, 25, 90])).replace("|", "/"))
                else:
                    row.append(r.choice(NAMES + ["12 Oct 2026", "", "Not specified",
                                                 "OMR 1,200.500"]))
            if r.random() < 0.1:
                row = row[:max(1, len(row) - 2)]              # a short row
            rows.append(row)
        out = ["| " + " | ".join(heads) + " |", "| " + " | ".join("---" for _ in heads) + " |"]
        return "\n".join(out + ["| " + " | ".join(x) + " |" for x in rows])

    def minutes(self) -> str:
        r, parts = self.r, []
        if r.random() < 0.9:
            parts.append("# " + r.choice(["Meeting Minutes", "Board Minutes", self.sent(12)]))
        if r.random() < 0.85:
            if r.random() < 0.85:
                parts.append("**Meeting Title:** " + r.choice(
                    [self.sent(4), self.sent(40), "Not specified", r.choice(LONG)]))
            for k in r.sample(["Date and Time", "Location", "Chair", "Minute Taker", "Project",
                               "A Very Long Label For A Detail Field Indeed"], r.randint(0, 6)):
                parts.append(f"**{k}:** " + r.choice([self.sent(5), self.sent(45),
                                                      "Not specified", ""]))
            if r.random() < 0.8:
                n = r.choice([0, 1, 3, 9, 40])
                parts.append("**Attendees:** " + (", ".join(r.choice(NAMES) for _ in range(n))
                                                  or "Not specified"))
            if r.random() < 0.3:
                parts.append("**Apologies:** " + ", ".join(r.sample(NAMES, 2)))
        for _ in range(r.randint(0, 9)):
            parts += ["", r.choice(["## ", "## ", "### ", "#### "]) + r.choice(
                ["Decisions Made", "Action Items", self.sent(3), self.sent(25)])]
            for _ in range(r.randint(0, 4)):
                kind = r.choice(["para", "bullets", "olist", "table", "kv", "para"])
                if kind == "para":
                    parts.append(" ".join(self.sent() for _ in range(r.choice([1, 3, 12, 40]))))
                elif kind == "bullets":
                    parts += ["- " + self.sent() for _ in range(r.choice([1, 4, 30]))]
                elif kind == "olist":
                    parts += [f"{i}. " + self.sent() for i in range(1, r.choice([2, 5, 25]))]
                elif kind == "kv":
                    parts.append(f"**{self.sent(2)[:-1]}:** {self.sent()}")
                else:
                    parts += ["", self.table(), ""]
                parts.append("")
        return "\n".join(parts)

    def profile(self, design: str):
        r = self.r
        if r.random() < 0.15:
            return None
        logo = r.choice([None, FP.ROOT / "assets" / "logo.png", FP._logo("fz_tall", 100, 500),
                         FP._logo("fz_wide", 1500, 80)])
        return FP.profile(
            name=r.choice(["Acme", "MICO360 Technologies LLC", self.sent(14)[:-1]]),
            address=r.choice(["", "Muscat, Oman", self.sent(30)]),
            phone=r.choice(["", "+968 2400 0000"]), email=r.choice(["", "info@example.com"]),
            website=r.choice(["", "www.example.com"]), logo_path=str(logo) if logo else "",
            logo_position=r.choice(["left", "center", "right"]),
            logo_width_mm=r.choice([10, 35, 60, 90]),
            footer_text=r.choice(["", "Confidential", self.sent(60)]),
            footer_alignment=r.choice(["left", "center", "right"]),
            show_page_numbers=r.random() < 0.85,
            page_number_position=r.choice(["footer-left", "footer-center", "footer-right",
                                           "header-left", "header-center", "header-right"]),
            page_number_format=r.choice(["Page {n} of {total}", "{n}/{total}", "- {n} -"]),
            accent_color=r.choice(["#8B1E1E", "#1F4E79", "#FFE066", "#000000", "#FFFFFF", "#0A7"]),
            pdf_design=design)


def lost_words(md: str, pages) -> list[str]:
    """Words of the minutes not found in the PDF text, ignoring where lines
    break (a long word may wrap). Tokens of 40+ characters are not checked: one
    can wrap over a page break, where the footer and running header text sit
    between its halves."""
    glued = "".join(re.sub(r"[^a-z0-9]+", "", p.text.lower()) for p in pages)
    out = set()
    for blk in md_blocks.parse(md):
        key = "" if blk.key.strip().lower() in P._TITLE_KEYS else blk.key
        for t in [blk.text, key, *blk.items, *blk.headers, *(c for row in blk.rows for c in row)]:
            for w in FP.words(P._visible(t)):
                if w not in glued and len(w) < 40:
                    out.add(w)
    return sorted(out)


def run(seed: int = 7, documents: int = 60, keep: Path | None = None) -> list[str]:
    """Returns one line per export that has a problem (empty = all clean)."""
    gen, bad = Gen(seed), []
    for case in range(documents):
        md = gen.minutes()
        for d in pdf_designs.choices():
            prof = gen.profile(d.key)
            try:
                path = P.export_pdf(md, FP.WORK / f"fz_{seed}_{case}_{d.key}.pdf", prof,
                                    design=None if prof else d.key)
                pages, _ = FP.read(path)
                problems = FP.out_of_bounds(pages)[:2] + FP.overlaps(pages)[:2]
                lost = lost_words(md, pages)
                if lost:
                    problems.append(f"missing words {lost[:5]}")
                if "■" in " ".join(p.text for p in pages):
                    problems.append("black box")
            except Exception as exc:                        # noqa: BLE001
                problems, path = [f"EXCEPTION {exc!r}"[:300]], None
            if problems:
                bad.append(f"seed {seed} case {case} {d.key}: {problems[:3]}")
                if keep is not None:
                    keep.mkdir(parents=True, exist_ok=True)
                    (keep / f"{seed}_{case}_{d.key}.md").write_text(md, encoding="utf-8")
    return bad


if __name__ == "__main__":
    import shutil
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    problems = run(seed, n, keep=Path.cwd() / "pdf_fuzz_failures")
    for line in problems:
        print(line[:300])
    print(f"\n==== PDF FUZZ: {n} documents x {len(pdf_designs.choices())} designs, "
          f"{len(problems)} with problems ====")
    sys.stdout.flush()
    shutil.rmtree(FP._TMP, ignore_errors=True)
    from mico360.hard_exit import hard_exit
    hard_exit(1 if problems else 0)
