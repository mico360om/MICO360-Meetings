"""Layout metrics — the spacing counterpart to theme.py's design tokens.

Every page uses these constants for margins and gaps instead of ad-hoc numbers,
so spacing is uniform across the app. The scale is a 4px grid:

    XS=4  SM=8  MD=12  LG=16  XL=20  XXL=24

Helpers return ready-to-use tuples/values for the two recurring containers:
a *page* (the scroll content) and a *card* (a panel inside it).
"""
from __future__ import annotations

# 4px spacing grid
XS, SM, MD, LG, XL, XXL = 4, 8, 12, 16, 20, 24

# Page = the scrolling content column. 24px sides (the consistency audit expects
# a uniform 24px content left-margin), a little more breathing room top/bottom.
PAGE_MARGINS = (XXL, XL, XXL, XXL)     # (l, t, r, b)
PAGE_GAP = MD                           # gap between page-level blocks

# Card = a surface panel inside a page.
CARD_MARGINS = (XL, XL, XL, XL)         # 20px all round
CARD_GAP = MD                           # gap between rows inside a card

# Dense sub-rows (button rows, inline controls).
ROW_GAP = SM


def page_margins() -> tuple[int, int, int, int]:
    return PAGE_MARGINS


def card_margins() -> tuple[int, int, int, int]:
    return CARD_MARGINS
