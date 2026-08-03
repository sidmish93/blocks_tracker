"""Tests for the per-company market metrics, using synthetic sessions (no network)."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.market import (  # noqa: E402
    Session,
    _corporate_action_factor,
    _measure,
    _window,
    apply_adjustments,
)

CRORE = 10_000_000


def session(day, close, quantity, value=None, delivery=None):
    """A trading day. Value defaults to a flat print at the closing price."""
    return Session(
        day=day,
        close=close,
        quantity=quantity,
        value=value if value is not None else close * quantity,
        delivery_qty=delivery if delivery is not None else quantity / 2,
    )


class CorporateActionParsing(unittest.TestCase):
    def test_face_value_split_uses_the_ratio_of_face_values(self):
        subject = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share"
        self.assertAlmostEqual(_corporate_action_factor(subject), 0.2)

    def test_split_wording_with_rupee_singular_is_understood(self):
        subject = "Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share"
        self.assertAlmostEqual(_corporate_action_factor(subject), 0.2)

    def test_bonus_is_shares_held_over_shares_after_the_issue(self):
        self.assertAlmostEqual(_corporate_action_factor("Bonus 1:1"), 0.5)
        self.assertAlmostEqual(_corporate_action_factor("Bonus 1:10"), 10 / 11)
        self.assertAlmostEqual(_corporate_action_factor("Bonus 4:1"), 0.2)

    def test_dividends_are_ignored_because_these_are_price_returns(self):
        self.assertIsNone(_corporate_action_factor("Interim Dividend - Rs 5 Per Share"))

    def test_rights_are_ignored_rather_than_adjusted_on_a_guess(self):
        self.assertIsNone(_corporate_action_factor("Rights 3:10 @ Premium Rs 50/-"))

    def test_unparseable_split_does_not_invent_a_factor(self):
        self.assertIsNone(_corporate_action_factor("Face Value Split (Sub-Division)"))


class Adjustment(unittest.TestCase):
    def test_only_sessions_before_the_ex_date_are_restated(self):
        sessions = [
            session(date(2026, 7, 23), 1574.5, 1000),
            session(date(2026, 7, 24), 323.2, 5000),
        ]
        apply_adjustments(sessions, [(date(2026, 7, 24), 0.2)])

        self.assertAlmostEqual(sessions[0].adjusted_close, 314.9)
        self.assertAlmostEqual(sessions[1].adjusted_close, 323.2)

    def test_quantity_is_restated_in_the_opposite_direction(self):
        sessions = [session(date(2026, 7, 23), 1574.5, 1000)]
        apply_adjustments(sessions, [(date(2026, 7, 24), 0.2)])

        # One old share is five new ones, so old volume counts five times over.
        self.assertAlmostEqual(sessions[0].adjusted_quantity, 5000)

    def test_two_events_in_the_window_compound(self):
        sessions = [session(date(2026, 7, 1), 400.0, 100)]
        apply_adjustments(sessions, [(date(2026, 7, 10), 0.5), (date(2026, 7, 20), 0.5)])

        self.assertAlmostEqual(sessions[0].adjusted_close, 100.0)


class WindowSelection(unittest.TestCase):
    def setUp(self):
        self.sessions = [session(date(2026, 6, day), 100.0, 10) for day in (22, 23, 24, 25, 26, 29)]

    def test_one_day_window_is_the_latest_session_measured_off_the_one_before(self):
        inside, reference = _window(self.sessions, date(2026, 6, 29), 1)

        self.assertEqual([item.day for item in inside], [date(2026, 6, 29)])
        self.assertEqual(reference.day, date(2026, 6, 26))

    def test_one_week_window_spans_seven_calendar_days_not_seven_sessions(self):
        inside, reference = _window(self.sessions, date(2026, 6, 29), 7)

        self.assertEqual(
            [item.day for item in inside],
            [date(2026, 6, 23), date(2026, 6, 24), date(2026, 6, 25), date(2026, 6, 26), date(2026, 6, 29)],
        )
        self.assertEqual(reference.day, date(2026, 6, 22))

    def test_no_reference_when_history_does_not_reach_back_far_enough(self):
        inside, reference = _window(self.sessions, date(2026, 6, 29), 30)

        self.assertEqual(len(inside), 6)
        self.assertIsNone(reference)


class Measures(unittest.TestCase):
    def setUp(self):
        # Five sessions ending Mon 29 Jun. The 1-week window covers 23-29 Jun and
        # is measured against the close on 22 Jun.
        self.sessions = [
            session(date(2026, 6, 22), 100.0, 1000, value=100.0 * 1000, delivery=400),
            session(date(2026, 6, 23), 110.0, 2000, value=110.0 * 2000, delivery=1000),
            session(date(2026, 6, 24), 120.0, 1000, value=120.0 * 1000, delivery=500),
            session(date(2026, 6, 25), 130.0, 1000, value=130.0 * 1000, delivery=300),
            session(date(2026, 6, 26), 140.0, 2000, value=140.0 * 2000, delivery=1200),
            session(date(2026, 6, 29), 150.0, 4000, value=150.0 * 4000, delivery=2000),
        ]
        self.measures = _measure(self.sessions)

    def test_reports_the_session_it_was_taken_from(self):
        self.assertEqual(self.measures["as_of"], "2026-06-29")
        self.assertEqual(self.measures["close"], 150.0)

    def test_one_day_return_is_against_the_previous_session(self):
        self.assertAlmostEqual(self.measures["return_1d_pct"], (150 / 140 - 1) * 100, places=2)

    def test_one_week_return_is_against_the_close_a_week_earlier(self):
        self.assertAlmostEqual(self.measures["return_1w_pct"], 50.0, places=2)

    def test_adtv_averages_traded_value_over_the_sessions_in_the_window(self):
        traded = 110 * 2000 + 120 * 1000 + 130 * 1000 + 140 * 2000 + 150 * 4000
        self.assertAlmostEqual(self.measures["adtv_1w_cr"], round(traded / 5 / CRORE, 2))

    def test_adtv_over_one_day_is_that_session_alone(self):
        self.assertAlmostEqual(self.measures["adtv_1d_cr"], round(150 * 4000 / CRORE, 2))

    def test_vwap_is_value_over_quantity_not_an_average_of_daily_prices(self):
        traded = 110 * 2000 + 120 * 1000 + 130 * 1000 + 140 * 2000 + 150 * 4000
        self.assertAlmostEqual(self.measures["vwap_1w"], round(traded / 10000, 2))

    def test_delivery_pools_the_window_rather_than_averaging_daily_percentages(self):
        delivered = 1000 + 500 + 300 + 1200 + 2000
        self.assertAlmostEqual(self.measures["delivery_1w_pct"], round(delivered / 10000 * 100, 2))

    def test_session_counts_are_reported_so_short_windows_are_visible(self):
        self.assertEqual(self.measures["sessions_1d"], 1)
        self.assertEqual(self.measures["sessions_1w"], 5)

    def test_a_window_with_no_earlier_close_reports_everything_but_the_return(self):
        self.assertIn("adtv_1m_cr", self.measures)
        self.assertNotIn("return_1m_pct", self.measures)


class UndisclosedDelivery(unittest.TestCase):
    """NSE publishes no deliverable data for the trade-for-trade series, which
    must read as unknown rather than as nothing having been delivered."""

    def test_delivery_is_omitted_when_no_session_reports_it(self):
        sessions = [
            Session(day=date(2026, 6, 26), close=500.0, quantity=1000, value=500_000),
            Session(day=date(2026, 6, 29), close=520.0, quantity=1000, value=520_000),
        ]
        measures = _measure(sessions)

        self.assertIn("adtv_1d_cr", measures)
        self.assertNotIn("delivery_1d_pct", measures)
        self.assertNotIn("delivery_1w_pct", measures)

    def test_delivery_covers_the_sessions_that_do_report_it(self):
        sessions = [
            session(date(2026, 6, 25), 100.0, 1000, delivery=600),
            Session(day=date(2026, 6, 26), close=100.0, quantity=5000, value=500_000),
            session(date(2026, 6, 29), 100.0, 1000, delivery=400),
        ]
        measures = _measure(sessions)

        self.assertAlmostEqual(measures["delivery_1w_pct"], 50.0)


class MeasuresAcrossASplit(unittest.TestCase):
    """A 5-for-1 split mid-window must not read as an 80% fall."""

    def setUp(self):
        self.sessions = [
            session(date(2026, 6, 22), 1500.0, 1000, value=1500.0 * 1000, delivery=500),
            session(date(2026, 6, 24), 1550.0, 1000, value=1550.0 * 1000, delivery=500),
            session(date(2026, 6, 26), 320.0, 5000, value=320.0 * 5000, delivery=2500),
            session(date(2026, 6, 29), 330.0, 5000, value=330.0 * 5000, delivery=2500),
        ]
        apply_adjustments(self.sessions, [(date(2026, 6, 26), 0.2)])
        self.measures = _measure(self.sessions)

    def test_return_is_measured_on_a_consistent_share_count(self):
        self.assertAlmostEqual(self.measures["return_1w_pct"], 10.0, places=2)

    def test_vwap_restates_pre_split_volume_so_it_stays_comparable(self):
        traded = 1550.0 * 1000 + 320.0 * 5000 + 330.0 * 5000
        quantity = 1000 / 0.2 + 5000 + 5000
        self.assertAlmostEqual(self.measures["vwap_1w"], round(traded / quantity, 2))

    def test_delivery_percentage_is_unaffected_by_the_split(self):
        self.assertAlmostEqual(self.measures["delivery_1w_pct"], 50.0, places=2)


if __name__ == "__main__":
    unittest.main()
