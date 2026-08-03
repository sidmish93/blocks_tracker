"""Takeaways from a quarterly filing — absolute figure plus how it moved."""

import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.results import _money, _prior_quarter, _quarter_label, takeaways  # noqa: E402

CRORE = 10_000_000


def filing(**fields):
    base = dict(
        q_revenue=None,
        q_pat=None,
        q_pbt=None,
        q_ebitda=None,
        q_ebit=None,
        q_depreciation=None,
        q_basic_eps=None,
        q_diluted_eps=None,
        q_other_income=None,
        q_finance_costs=None,
        q_exceptional_items=None,
        ytd_revenue=None,
        ytd_pat=None,
        debt_equity_ratio=None,
        period_start=None,
        period_end=None,
    )
    base.update(fields)
    return SimpleNamespace(**base)


class QuarterLabel(unittest.TestCase):
    def test_indian_financial_year_quarters(self):
        self.assertEqual(_quarter_label(date(2026, 6, 30)), "Q1 FY27 (Jun 2026)")
        self.assertEqual(_quarter_label(date(2026, 3, 31)), "Q4 FY26 (Mar 2026)")


class Money(unittest.TestCase):
    def test_crores_are_grouped_the_indian_way(self):
        self.assertEqual(_money(28_499_990_000), "Rs 2,850.00 cr")
        self.assertEqual(_money(723_970_000), "Rs 72.40 cr")


class Takeaways(unittest.TestCase):
    def test_each_line_carries_the_rupee_figure_and_the_move(self):
        points = takeaways(
            filing(
                q_revenue=28_500_000_000,
                q_ebitda=2_395_900_000,
                q_pat=724_000_000,
                q_basic_eps=0.97,
            ),
            filing(
                q_revenue=21_923_000_000,
                q_ebitda=1_973_000_000,
                q_pat=725_500_000,
                q_basic_eps=0.97,
            ),
        )
        self.assertTrue(any(point.startswith("Revenue Rs 2,850.00 cr, up 30% YoY") for point in points))
        ebitda = next(point for point in points if point.startswith("EBITDA"))
        self.assertIn("Rs 239.59 cr", ebitda)
        self.assertIn("8.4% margin", ebitda)
        self.assertIn("up 21% YoY", ebitda)
        self.assertIn("margin down", ebitda)
        self.assertTrue(any(point.startswith("PAT Rs 72.40 cr") for point in points))
        self.assertTrue(any(point.startswith("EPS Rs 0.97") for point in points))

    def test_revenue_up_profits_down_is_called_out(self):
        points = takeaways(
            filing(q_revenue=100 * CRORE, q_ebitda=15 * CRORE, q_pat=8 * CRORE, q_basic_eps=1.0),
            filing(q_revenue=80 * CRORE, q_ebitda=14 * CRORE, q_pat=10 * CRORE, q_basic_eps=1.2),
        )
        self.assertTrue(any("Revenue rose but profits did not" in point for point in points))

    def test_profits_lagging_sales_is_called_out(self):
        points = takeaways(
            filing(q_revenue=130 * CRORE, q_ebitda=18 * CRORE, q_pat=11 * CRORE, q_basic_eps=1.1),
            filing(q_revenue=100 * CRORE, q_ebitda=14 * CRORE, q_pat=10 * CRORE, q_basic_eps=1.0),
        )
        # Revenue +30%, PAT +10% — profits still rose, but not with sales.
        self.assertTrue(any("Profits lagged sales" in point for point in points))

    def test_a_turnaround_keeps_the_absolute_profit(self):
        points = takeaways(
            filing(q_revenue=100 * CRORE, q_ebitda=20 * CRORE, q_pat=10 * CRORE),
            filing(q_revenue=90 * CRORE, q_ebitda=15 * CRORE, q_pat=-5 * CRORE),
        )
        self.assertTrue(any("turned profitable" in point and "Rs 10.00 cr" in point for point in points))

    def test_a_widening_loss_keeps_the_size(self):
        points = takeaways(
            filing(q_revenue=100 * CRORE, q_ebitda=-5 * CRORE, q_pat=-20 * CRORE),
            filing(q_revenue=100 * CRORE, q_ebitda=-4 * CRORE, q_pat=-10 * CRORE),
        )
        self.assertTrue(any(point.startswith("Loss Rs 20.00 cr, widened") for point in points))

    def test_material_other_income_and_exceptionals_are_flagged(self):
        points = takeaways(
            filing(
                q_revenue=100 * CRORE,
                q_ebitda=20 * CRORE,
                q_pat=12 * CRORE,
                q_other_income=8 * CRORE,
                q_exceptional_items=5 * CRORE,
            )
        )
        self.assertTrue(any(point.startswith("Other income Rs 8.00 cr") for point in points))
        self.assertTrue(any(point.startswith("Exceptional gain Rs 5.00 cr") for point in points))

    def test_fytd_is_kept_when_it_adds_beyond_the_quarter(self):
        points = takeaways(
            filing(
                q_revenue=28_500_000_000,
                q_pat=724_000_000,
                q_ebitda=2_400_000_000,
                ytd_revenue=105_083_070_000,
                ytd_pat=1_525_400_000,
            ),
            filing(
                q_revenue=22_000_000_000,
                q_pat=700_000_000,
                q_ebitda=2_000_000_000,
                ytd_revenue=90_000_000_000,
                ytd_pat=1_200_000_000,
            ),
        )
        self.assertTrue(any(point.startswith("FYTD revenue") for point in points))

    def test_ebitda_is_rebuilt_from_ebit_and_depreciation_when_blank(self):
        points = takeaways(
            filing(
                q_revenue=100 * CRORE,
                q_ebit=5 * CRORE,
                q_depreciation=15 * CRORE,
                q_pat=3 * CRORE,
            )
        )
        self.assertTrue(any("EBITDA Rs 20.00 cr" in point for point in points))

    def test_lodha_style_strong_quarter(self):
        points = takeaways(
            filing(
                q_revenue=49_967_000_000,
                q_ebitda=18_433_000_000,
                q_pat=13_731_000_000,
                q_basic_eps=13.73,
            ),
            filing(
                q_revenue=34_925_000_000,
                q_ebitda=9_696_000_000,
                q_pat=6_751_000_000,
                q_basic_eps=6.76,
            ),
        )
        self.assertTrue(any("Revenue Rs 4,996.70 cr, up 43% YoY" in point for point in points))
        self.assertTrue(any("PAT Rs 1,373.10 cr" in point and "more than doubled" in point for point in points))
        self.assertTrue(any("Profits grew faster than sales" in point for point in points))

    def test_qoq_wording_when_year_ago_filing_is_missing(self):
        # Adani Green’s Jun-2026 print has no Jun-2025 on NSE, so we fall back to QoQ.
        points = takeaways(
            filing(
                q_revenue=4_431 * CRORE,
                q_ebitda=2_216 * CRORE,
                q_pat=983 * CRORE,
                q_basic_eps=5.05,
            ),
            filing(
                q_revenue=3_000 * CRORE,
                q_ebitda=1_500 * CRORE,
                q_pat=700 * CRORE,
                q_basic_eps=3.50,
            ),
            versus="QoQ",
        )
        self.assertTrue(any("up 48% QoQ" in point for point in points))
        self.assertFalse(any("YoY" in point for point in points))


class PriorQuarter(unittest.TestCase):
    def test_it_picks_the_most_recent_earlier_quarter(self):
        rows = [
            {"_ended": date(2026, 6, 30), "consolidated": "Consolidated"},
            {"_ended": date(2026, 3, 31), "consolidated": "Standalone"},
            {"_ended": date(2026, 3, 31), "consolidated": "Consolidated"},
            {"_ended": date(2025, 12, 31), "consolidated": "Consolidated"},
        ]
        found = _prior_quarter(rows, date(2026, 6, 30))
        self.assertEqual(found["_ended"], date(2026, 3, 31))
        self.assertTrue(found["consolidated"].lower().startswith("consol"))


if __name__ == "__main__":
    unittest.main()
