"""The downloadable document: what fits on the page, and what the page can draw.

Building a PDF costs milliseconds and needs nothing from the network, so these
build real documents and read the structure back out of the bytes.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pdf import (  # noqa: E402
    DEAL_WIDTHS,
    MARKET_WIDTHS,
    NEWS_WIDTHS,
    WIDTH,
    _clean,
    _day,
    _indian,
    _loses_words,
    _render,
    _signed,
    _story,
    build_pdf,
)
from reportlab.lib.units import mm  # noqa: E402

DEAL = {
    "company_name": "Genus Power Infrastructures Limited",
    "trade_date": "30-Jun-2026",
    "trade_date_iso": "2026-06-30",
    "ticker": "GENUSPOWER",
    "quantity": 33559114,
    "price": "282.15 - 290.00",
    "deal_size_cr": 955.61,
    "deal_type": "Bulk; Block; Both Block and Bulk",
    "exchange": "BSE + NSE",
    "sellers": "CHISWICK INVESTMENT PTE LTD (955.61 cr)",
    "buyers": "PROFITEX SHARES & SECURITIES PRIVATE LIMITED (130.00 cr)",
    "prev_close": 318.45,
    "discount_pct": 10.58,
    "pre_return_1m_pct": 1.64,
    "pre_adtv_1m_cr": 48.36,
    "pre_vwap_1m": 332.08,
    "pre_delivery_1m_pct": 38.0,
}
QUOTE = {
    "company_name": "Genus Power Infrastructures Limited",
    "ticker": "GENUSPOWER",
    "as_of": "2026-08-03",
    "close": 316.0,
    "market_cap_cr": 9622,
    "return_1d_pct": 1.06,
    "return_1w_pct": -1.86,
    "return_1m_pct": -1.7,
    "adtv_1d_cr": 29.14,
    "adtv_1w_cr": 37.77,
    "adtv_1m_cr": 67.69,
    "vwap_1d": 317.2,
    "vwap_1w": 313.77,
    "vwap_1m": 316.95,
    "delivery_1d_pct": 52.84,
    "delivery_1w_pct": 54.84,
    "delivery_1m_pct": 46.01,
}
STORY = {
    "company_name": "Genus Power Infrastructures Limited",
    "source": "Moneycontrol",
    "published": "2026-08-03T05:19:00",
    "headline": "Genus Power wins \u20b91,900 crore order",
    "url": "https://www.moneycontrol.com/news/genus-power.html",
}
META = {
    "from_date": "2026-06-01",
    "to_date": "2026-06-30",
    "min_deal_size_cr": 200,
    "deal_count": 1,
    "total_value_cr": 955.61,
    "companies": [{"name": "Genus Power Infrastructures Limited", "ticker": "GENUSPOWER"}],
}


def pages(raw) -> int:
    # Every page dictionary says /Type /Page; the one node above them all says
    # /Type /Pages and matches the same prefix.
    return raw.count(b"/Type /Page") - raw.count(b"/Type /Pages")


def bookmarks(raw) -> list:
    found = [title.decode("latin-1") for title in re.findall(rb"/Title \(([^)]*)\)", raw)]
    return found[1:]  # the first is the document's own title


class WhatFitsAcrossThePage(unittest.TestCase):
    """A column narrower than its widest value spills into its neighbour, and a
    table wider than the page runs off the edge. Neither shows up as an error."""

    def test_every_table_is_exactly_as_wide_as_the_page(self):
        from app.pdf import _market_widths

        for name, widths in (
            ("market", MARKET_WIDTHS),
            ("market-1w-only", _market_widths(["1w"])),
            ("market-two-windows", _market_widths(["1w", "1m"])),
            ("deals", DEAL_WIDTHS),
            ("news", NEWS_WIDTHS),
        ):
            with self.subTest(name):
                self.assertAlmostEqual(sum(widths) * mm, WIDTH, places=3)

    def test_fewer_windows_magnifies_every_column_evenly(self):
        from app.pdf import _COMPANY, _market_natural_widths, _market_scale, _market_widths

        natural = _market_natural_widths(["1w"])
        scaled = _market_widths(["1w"])
        scale = _market_scale(["1w"])
        full_scale = _market_scale(["1d", "1w", "1m"])

        # Natural company width stays content-sized; page fill comes from scale.
        self.assertEqual(natural[0], _COMPANY)
        self.assertLess(natural[0], 60)
        self.assertGreater(scale, full_scale)
        self.assertGreater(scale, 1.3)

        # Every column grows by the same factor — not just the company name.
        for base, wide in zip(natural, scaled):
            self.assertAlmostEqual(wide / base, scale, places=5)

    def test_the_widest_ticker_clears_the_column_beside_it(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth

        for name, widths, column, size in (
            ("market", MARKET_WIDTHS, 0, 6.5),  # company name column
            ("deals", DEAL_WIDTHS, 2, 7.5),
        ):
            with self.subTest(name):
                drawn = stringWidth("ALTIUSINVIT", "Helvetica", size)
                self.assertLess(drawn + 6, widths[column] * mm)


class Numbers(unittest.TestCase):
    def test_digits_are_grouped_the_indian_way(self):
        self.assertEqual(_indian(33559114), "3,35,59,114")
        self.assertEqual(_indian(1068.04, 2), "1,068.04")
        self.assertEqual(_indian(955.61, 2), "955.61")
        self.assertEqual(_indian(0), "0")

    def test_a_negative_keeps_its_sign_in_front_of_the_grouping(self):
        self.assertEqual(_indian(-14444800), "-1,44,44,800")

    def test_a_movement_carries_its_direction(self):
        self.assertEqual(_signed(6.45), "+6.45")
        self.assertEqual(_signed(-2.38), "-2.38")

    def test_a_deal_struck_at_the_close_is_flat_rather_than_minus_nothing(self):
        # Rounding a hair below the close would otherwise print "-0.00".
        self.assertEqual(_signed(-0.0001), "0.00")
        self.assertEqual(_signed(0.0), "0.00")

    def test_nothing_measured_shows_as_a_dash(self):
        self.assertEqual(_indian(None), "\u2014")
        self.assertEqual(_signed(None), "\u2014")
        self.assertEqual(_day(None), "\u2014")

    def test_dates_are_written_out(self):
        self.assertEqual(_day("2026-08-03"), "03 Aug 2026")


class WhatThePageCanDraw(unittest.TestCase):
    """The built-in PDF fonts cover Western European text. Anything else lands
    on the page as a black box unless it is dealt with first."""

    def test_the_rupee_sign_becomes_the_letters_used_everywhere_else(self):
        self.assertEqual(_clean("\u20b91,900 crore"), "Rs 1,900 crore")

    def test_typographic_punctuation_is_flattened(self):
        self.assertEqual(_clean("Adani \u2013 \u201cno comment\u201d"), 'Adani - "no comment"')

    def test_a_headline_in_another_script_keeps_the_names_inside_it(self):
        headline = "Q1 Results: KIMS, Torrent Power \u0914\u0930 DOMS Industries \u0924\u0940\u0928"
        self.assertEqual(_clean(headline), "Q1 Results: KIMS, Torrent Power DOMS Industries")

    def test_a_feed_that_arrives_already_broken_loses_the_damage(self):
        self.assertEqual(_clean("Fidelity Funds \ufffd India Focus Fund"),
                         "Fidelity Funds India Focus Fund")

    def test_dropping_letters_is_worth_saying_and_tidying_punctuation_is_not(self):
        self.assertTrue(_loses_words("Torrent Power \u0914\u0930 DOMS"))
        self.assertFalse(_loses_words("\u20b91,900 crore \u2013 \u201cbig\u201d"))


class TheDocument(unittest.TestCase):
    def build(self, rows=(DEAL,), quotes=(QUOTE,), stories=(STORY,), meta=None):
        return build_pdf(list(rows), list(quotes), list(stories), {**META, **(meta or {})})

    def test_it_is_a_pdf(self):
        self.assertTrue(self.build().startswith(b"%PDF"))

    def test_cover_appears_only_when_deals_are_included(self):
        from reportlab.platypus import Paragraph

        def titles(meta):
            return [
                flow.getPlainText()
                for flow in _story([DEAL], [QUOTE], [STORY], meta)
                if isinstance(flow, Paragraph) and flow.style.name == "title"
            ]

        self.assertEqual(titles(META), ["Block & Bulk Deals Tracker"])
        market_only = {
            **META,
            "sections": {"deals": False, "market": True, "news": False, "quarters": False},
        }
        self.assertEqual(titles(market_only), [])

    def test_big_live_moves_are_flagged_beyond_three_percent(self):
        from app.pdf import _big_live_move

        self.assertTrue(_big_live_move({"intraday_return_pct": 3.01, "daily_return_pct": 0.1}))
        self.assertTrue(_big_live_move({"intraday_return_pct": 0.1, "daily_return_pct": -3.01}))
        self.assertFalse(_big_live_move({"intraday_return_pct": 3.0, "daily_return_pct": -3.0}))
        self.assertFalse(_big_live_move({"intraday_return_pct": None, "daily_return_pct": None}))
        self.assertTrue(_big_live_move({"highlight_row": True}))
        self.assertFalse(
            _big_live_move(
                {
                    "highlight_row": False,
                    "intraday_return_pct": 9.0,
                    "daily_return_pct": -9.0,
                }
            )
        )

    def test_footer_drops_deals_branding_when_deals_are_off(self):
        from app.pdf import _footer

        with_deals = _footer(META)
        self.assertIn("Block & Bulk Deals Tracker", with_deals)
        self.assertIn("01 Jun 2026", with_deals)

        market_only = {
            **META,
            "sections": {"deals": False, "market": True, "news": False, "quarters": False},
        }
        text = _footer(market_only)
        self.assertEqual(text, "Market data  |  Genus Power Infrastructures Limited")
        self.assertNotIn("Block & Bulk", text)
        self.assertNotIn("Jun 2026", text)

    def test_sections_have_no_commentary_notes(self):
        from reportlab.platypus import Paragraph

        story = _story(
            [DEAL],
            [QUOTE],
            [STORY],
            {
                **META,
                "market_notes": ["NSE CMP from NSE session (2026-08-04 NSE session)."],
                "sections": {"deals": False, "market": True, "news": False, "quarters": False},
            },
        )
        notes = [
            flow.getPlainText()
            for flow in story
            if isinstance(flow, Paragraph) and flow.style.name == "note"
        ]
        self.assertFalse(any("current snapshot" in text.lower() for text in notes))
        self.assertFalse(any("NSE CMP from NSE session" in text for text in notes))

    def test_the_sections_are_bookmarked_in_the_order_asked_for(self):
        self.assertEqual(
            bookmarks(self.build()),
            ["Market data", "Latest quarter", "Block & bulk deals", "News"],
        )

    def test_sections_flow_continuously_without_forced_page_breaks(self):
        # Four thin sections used to each force a new page; they should now share fewer.
        self.assertLess(pages(self.build()), 4)

    def test_a_search_that_found_nothing_still_explains_every_section(self):
        raw = build_pdf([], [], [], {**META, "deal_count": 0, "total_value_cr": 0})
        self.assertLess(pages(raw), 4)
        self.assertEqual(
            bookmarks(raw),
            ["Market data", "Latest quarter", "Block & bulk deals", "News"],
        )

    def test_quarter_takeaways_appear_in_the_document(self):
        quarter = {
            "company_name": "Genus Power Infrastructures Limited",
            "ticker": "GENUSPOWER",
            "quarter": "Q1 FY27 (Jun 2026)",
            "consolidated": "Consolidated",
            "audited": "Un-Audited",
            "report_url": "https://nsearchives.nseindia.com/corporate/example.pdf",
            "report_label": "Results PDF",
            "takeaways": ["Revenue up 30% YoY", "EBITDA margin 8.4% (up 1.2 pt YoY)"],
        }
        raw = build_pdf([DEAL], [QUOTE], [STORY], META, [quarter])
        # Body text is compressed in the stream; the outline title and the link
        # annotation sit in the clear, and those are what a reader jumps to.
        self.assertIn("Latest quarter", bookmarks(raw))
        self.assertIn(b"nsearchives.nseindia.com/corporate/example.pdf", raw)

    def test_a_deal_with_no_month_behind_it_still_prints(self):
        blank = {**DEAL, "prev_close": None, "discount_pct": None, "pre_return_1m_pct": None,
                 "pre_adtv_1m_cr": None, "pre_vwap_1m": None, "pre_delivery_1m_pct": None,
                 "buyers": ""}
        self.assertLess(pages(self.build(rows=[blank])), 4)

    def test_the_footer_counts_the_pages_the_document_actually_has(self):
        # The total is only known after a first run, and stamping it must not
        # change where anything falls, or the count would be wrong again.
        story = lambda: _story([DEAL], [QUOTE], [STORY], META)  # noqa: E731
        _, first = _render(story(), "footer", 0)
        raw, second = _render(story(), "footer", first)
        self.assertEqual(first, second)
        self.assertEqual(pages(raw), first)


if __name__ == "__main__":
    unittest.main()
