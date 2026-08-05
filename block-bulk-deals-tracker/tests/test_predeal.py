"""The month of trading before a deal, and the discount it printed at.

Synthetic sessions only, so the arithmetic is checked without the network.
"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.market import Session  # noqa: E402
from app.predeal import _chunks_needed, _quarter_of, deal_price, discount, measure  # noqa: E402

CRORE = 10_000_000
DEAL_DAY = date(2026, 7, 1)


def session(day, close, quantity=100_000, delivery=None):
    return Session(
        day=day,
        close=close,
        quantity=quantity,
        value=close * quantity,
        delivery_qty=delivery,
    )


def run_up(days=40, close=100.0, quantity=100_000, delivery=None, end=DEAL_DAY):
    """A session a day, oldest first, ending the day before the deal."""
    return [
        session(end - timedelta(days=offset), close, quantity, delivery)
        for offset in range(days, 0, -1)
    ]


class TheWindow(unittest.TestCase):
    def test_it_ends_on_the_last_session_before_the_deal(self):
        found = measure(run_up(), [], DEAL_DAY)
        self.assertEqual(found["prev_close_day"], (DEAL_DAY - timedelta(days=1)).isoformat())

    def test_the_deal_day_itself_is_never_counted(self):
        # The deal moves the tape, so including it would measure the deal against
        # itself rather than against the month it landed in.
        sessions = run_up() + [session(DEAL_DAY, 500.0, 9_000_000)]
        found = measure(sessions, [], DEAL_DAY)
        self.assertEqual(found["prev_close"], 100.0)
        self.assertEqual(found["pre_vwap_1m"], 100.0)

    def test_a_deal_after_a_weekend_measures_from_the_friday(self):
        monday = date(2026, 7, 6)
        friday = date(2026, 7, 3)
        sessions = run_up(end=friday + timedelta(days=1))
        found = measure(sessions, [], monday)
        self.assertEqual(found["prev_close_day"], friday.isoformat())

    def test_it_reaches_thirty_calendar_days_back(self):
        found = measure(run_up(days=60), [], DEAL_DAY)
        # A session a day, so thirty calendar days of them.
        self.assertEqual(found["pre_sessions_1m"], 30)

    def test_a_company_that_had_not_traded_yet_reports_nothing(self):
        later = [session(DEAL_DAY + timedelta(days=offset), 100.0) for offset in range(5)]
        self.assertEqual(measure(later, [], DEAL_DAY), {})

    def test_a_long_weekend_before_the_deal_is_still_measured(self):
        sessions = run_up(days=40, end=DEAL_DAY - timedelta(days=3))
        self.assertTrue(measure(sessions, [], DEAL_DAY))

    def test_a_close_from_weeks_earlier_is_refused_rather_than_used(self):
        # Sterlite Technologies leaves NSE's EQ archive in May and keeps trading
        # elsewhere. Measuring a June block off the stale May close read as a 53%
        # premium, so a gap this size is reported as no figures at all.
        stale = run_up(days=40, end=DEAL_DAY - timedelta(days=21))
        self.assertEqual(measure(stale, [], DEAL_DAY), {})


class Chunking(unittest.TestCase):
    """NSE hands back at most 70 sessions and picks the most recent ones itself,
    so history is asked for a quarter at a time or older deals come back blank."""

    def test_each_quarter_runs_to_its_last_day(self):
        self.assertEqual(_quarter_of(date(2026, 2, 14)), (date(2026, 1, 1), date(2026, 3, 31)))
        self.assertEqual(_quarter_of(date(2026, 4, 1)), (date(2026, 4, 1), date(2026, 6, 30)))
        self.assertEqual(_quarter_of(date(2026, 12, 31)), (date(2026, 10, 1), date(2026, 12, 31)))

    def test_no_chunk_is_wide_enough_to_be_truncated(self):
        # Around 62 sessions to a quarter, against a cap of 70.
        for start, end in _chunks_needed([date(2024, 3, 5), date(2026, 8, 20)]):
            self.assertLessEqual((end - start).days + 1, 92)

    def test_a_deal_reaches_back_into_the_quarter_before_it(self):
        # A deal on 5 April is measured over a window opening in February.
        self.assertEqual(
            _chunks_needed([date(2026, 4, 5)]),
            [(date(2026, 1, 1), date(2026, 3, 31)), (date(2026, 4, 1), date(2026, 6, 30))],
        )

    def test_deals_sharing_a_quarter_are_fetched_once(self):
        together = _chunks_needed([date(2026, 5, 4), date(2026, 5, 20), date(2026, 6, 30)])
        self.assertEqual(len(together), 2)

    def test_years_apart_do_not_drag_in_the_years_between(self):
        # November 2022 reaches back into the third quarter, and nothing at all is
        # asked for across 2023, 2024 and 2025.
        spread = _chunks_needed([date(2022, 11, 21), date(2026, 6, 24)])
        self.assertEqual(
            spread,
            [
                (date(2022, 7, 1), date(2022, 9, 30)),
                (date(2022, 10, 1), date(2022, 12, 31)),
                (date(2026, 4, 1), date(2026, 6, 30)),
            ],
        )


class Figures(unittest.TestCase):
    def test_the_return_runs_to_the_close_before_the_deal(self):
        sessions = run_up(days=60, close=100.0)
        for item in sessions[-30:]:
            item.close, item.value = 110.0, 110.0 * item.quantity
        found = measure(sessions, [], DEAL_DAY)
        self.assertEqual(found["pre_return_1m_pct"], 10.0)

    def test_adtv_averages_over_the_sessions_in_the_window(self):
        # 100 rupees on a lakh of shares is a crore a day.
        found = measure(run_up(close=100.0, quantity=100_000), [], DEAL_DAY)
        self.assertEqual(found["pre_adtv_1m_cr"], 1.0)

    def test_vwap_weights_by_volume_not_by_day(self):
        sessions = run_up(days=2)
        sessions[0].close, sessions[0].quantity = 100.0, 100_000
        sessions[0].value = 100.0 * 100_000
        sessions[1].close, sessions[1].quantity = 200.0, 900_000
        sessions[1].value = 200.0 * 900_000
        found = measure(sessions, [], DEAL_DAY)
        # A flat average would say 150; the heavy day at 200 pulls it to 190.
        self.assertEqual(found["pre_vwap_1m"], 190.0)

    def test_delivery_is_a_ratio_over_the_whole_window(self):
        found = measure(run_up(quantity=100_000, delivery=40_000), [], DEAL_DAY)
        self.assertEqual(found["pre_delivery_1m_pct"], 40.0)

    def test_delivery_is_blank_where_the_exchange_publishes_none(self):
        found = measure(run_up(delivery=None), [], DEAL_DAY)
        self.assertNotIn("pre_delivery_1m_pct", found)


class CorporateActions(unittest.TestCase):
    """A split inside the window must not read as a collapse in the price."""

    def setUp(self):
        # Five-for-one on 20 June: 500 before, 100 after, same company.
        self.split = [(date(2026, 6, 20), 0.2)]
        self.sessions = []
        for offset in range(40, 0, -1):
            day = DEAL_DAY - timedelta(days=offset)
            before = day < date(2026, 6, 20)
            close = 500.0 if before else 100.0
            quantity = 20_000 if before else 100_000
            self.sessions.append(session(day, close, quantity))

    def test_the_return_is_flat_across_a_split(self):
        found = measure(self.sessions, self.split, DEAL_DAY)
        self.assertEqual(found["pre_return_1m_pct"], 0.0)

    def test_an_unadjusted_reading_would_have_called_it_a_crash(self):
        found = measure(self.sessions, [], DEAL_DAY)
        self.assertEqual(found["pre_return_1m_pct"], -80.0)

    def test_vwap_comes_back_in_the_prices_of_the_deal_date(self):
        found = measure(self.sessions, self.split, DEAL_DAY)
        self.assertEqual(found["pre_vwap_1m"], 100.0)

    def test_a_split_after_the_deal_leaves_the_window_alone(self):
        # Restating into today's share count would misprice a deal struck before it.
        later = [(date(2026, 8, 1), 0.2)]
        self.assertEqual(
            measure(self.sessions, later, DEAL_DAY), measure(self.sessions, [], DEAL_DAY)
        )


class DealPrice(unittest.TestCase):
    def row(self, quantity, deal_size_cr):
        return {"quantity": quantity, "deal_size_cr": deal_size_cr}

    def test_the_price_is_the_deal_weighted_by_quantity(self):
        # 33,559,114 shares for 955.61 cr is 284.75, not the midpoint of the range.
        self.assertAlmostEqual(deal_price(self.row(33_559_114, 955.61)), 284.75, places=2)

    def test_a_row_with_no_quantity_has_no_price(self):
        self.assertEqual(deal_price(self.row(0, 100.0)), 0.0)


class Discount(unittest.TestCase):
    def row(self, price, quantity=1_000_000):
        return {"quantity": quantity, "deal_size_cr": price * quantity / CRORE}

    def test_a_block_below_the_last_close_reads_positive(self):
        self.assertEqual(discount(self.row(90.0), 100.0), 10.0)

    def test_a_block_above_the_last_close_reads_negative(self):
        self.assertEqual(discount(self.row(100.0), 90.0), -11.11)

    def test_a_block_at_the_last_close_is_flat(self):
        self.assertEqual(discount(self.row(100.0), 100.0), 0.0)

    def test_no_close_means_no_discount(self):
        self.assertIsNone(discount(self.row(90.0), None))
        self.assertIsNone(discount(self.row(90.0), 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
