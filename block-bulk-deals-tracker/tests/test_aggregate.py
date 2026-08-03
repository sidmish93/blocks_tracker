"""Rule-level tests for the tracker aggregation, using synthetic legs (no network)."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import aggregate  # noqa: E402
from app.aggregate import build_rows  # noqa: E402
from app.sources import BLOCK, BULK, Leg  # noqa: E402

# Keep these tests offline and deterministic: the synthetic scrips below are not
# in either exchange's master, so identity falls back to the security name.
aggregate.nse_lookup = lambda symbol: {}
aggregate.bse_lookup = lambda scrip_code: {}

DAY = date(2026, 6, 30)
CRORE = 10_000_000


def leg(exchange, deal_type, side, quantity, price, client, symbol="TESTCO"):
    return Leg(
        exchange=exchange,
        deal_type=deal_type,
        trade_date=DAY,
        symbol=symbol,
        security_name=f"{symbol} Limited",
        scrip_code=f"TEST-{symbol}",
        client=client,
        side=side,
        quantity=quantity,
        price=price,
    )


def only_row(legs, threshold=0):
    rows, stats = build_rows(legs, threshold)
    assert len(rows) == 1, f"expected exactly one row, got {len(rows)}"
    return rows[0], stats


class DealSizeAndThreshold(unittest.TestCase):
    def test_deal_size_uses_the_larger_disclosed_side(self):
        # Only the seller crossed the disclosure threshold; the buy side is partial.
        row, _ = only_row(
            [
                leg("NSE", BULK, "SELL", 1_000_000, 1000, "SELLER A"),
                leg("NSE", BULK, "BUY", 400_000, 1000, "BUYER A"),
            ]
        )
        self.assertEqual(row["deal_size_cr"], 100.0)
        self.assertEqual(row["quantity"], 1_000_000)

    def test_threshold_applies_to_the_aggregated_row_not_single_legs(self):
        legs = [
            leg("NSE", BULK, "SELL", 600_000, 1000, "SELLER A"),
            leg("NSE", BULK, "SELL", 600_000, 1000, "SELLER B"),
        ]
        # Neither leg alone is 100 cr, but together they are 120 cr.
        self.assertEqual(len(build_rows(legs, 100)[0]), 1)
        self.assertEqual(len(build_rows(legs, 150)[0]), 0)


class SelfTradeExclusion(unittest.TestCase):
    def test_entity_on_both_sides_is_dropped_but_others_survive(self):
        row, stats = only_row(
            [
                leg("NSE", BULK, "BUY", 900_000, 1000, "HFT PRIVATE LIMITED"),
                leg("NSE", BULK, "SELL", 950_000, 1000, "HFT PRIVATE LIMITED"),
                leg("NSE", BULK, "SELL", 1_000_000, 1000, "GENUINE SELLER LIMITED"),
                leg("NSE", BULK, "BUY", 1_000_000, 1000, "GENUINE BUYER LIMITED"),
            ]
        )
        self.assertNotIn("HFT", row["buyers"])
        self.assertNotIn("HFT", row["sellers"])
        self.assertIn("GENUINE SELLER LIMITED", row["sellers"])
        self.assertEqual(row["deal_size_cr"], 100.0)
        self.assertEqual(stats["self_trade_entities_excluded"], 1)

    def test_punctuation_differences_still_count_as_the_same_entity(self):
        rows, stats = build_rows(
            [
                leg("NSE", BULK, "BUY", 1_000_000, 1000, "MORGAN STANLEY ASIA SINGAPORE PTE"),
                leg("BSE", BULK, "SELL", 1_000_000, 1000, "MORGAN STANLEY ASIA (SINGAPORE) PTE."),
            ],
            0,
        )
        self.assertEqual(rows, [])
        self.assertEqual(stats["self_trade_entities_excluded"], 1)


class BulkBlockDeduplication(unittest.TestCase):
    def test_identical_bulk_and_block_rows_are_counted_once(self):
        row, _ = only_row(
            [
                leg("NSE", BLOCK, "SELL", 1_000_000, 1000, "SELLER A"),
                leg("NSE", BULK, "SELL", 1_000_000, 1000, "SELLER A"),
            ]
        )
        self.assertEqual(row["deal_size_cr"], 100.0)
        self.assertEqual(row["deal_type"], "Both Block and Bulk")

    def test_one_aggregated_bulk_row_matches_many_itemised_block_rows(self):
        # This is how BSE reports: a single bulk row covering several block trades.
        row, _ = only_row(
            [
                leg("BSE", BLOCK, "SELL", 600_000, 1000, "SELLER A"),
                leg("BSE", BLOCK, "SELL", 400_000, 1000, "SELLER A"),
                leg("BSE", BULK, "SELL", 1_000_000, 1000, "SELLER A"),
            ]
        )
        self.assertEqual(row["deal_size_cr"], 100.0)
        self.assertEqual(row["deal_type"], "Both Block and Bulk")

    def test_bulk_quantity_beyond_the_block_is_kept_as_a_bulk_residual(self):
        row, _ = only_row(
            [
                leg("NSE", BLOCK, "SELL", 600_000, 1000, "SELLER A"),
                leg("NSE", BULK, "SELL", 1_000_000, 1000, "SELLER A"),
            ]
        )
        self.assertEqual(row["deal_size_cr"], 100.0)
        self.assertEqual(row["deal_type"], "Bulk; Both Block and Bulk")

    def test_a_block_only_deal_stays_block(self):
        row, _ = only_row([leg("NSE", BLOCK, "SELL", 1_000_000, 1000, "SELLER A")])
        self.assertEqual(row["deal_type"], "Block")

    def test_deduplication_does_not_cross_exchanges(self):
        # The same shares cannot trade on both exchanges, so these are two tranches.
        row, _ = only_row(
            [
                leg("NSE", BULK, "SELL", 1_000_000, 1000, "SELLER A"),
                leg("BSE", BULK, "SELL", 1_000_000, 1000, "SELLER A"),
            ]
        )
        self.assertEqual(row["deal_size_cr"], 200.0)
        self.assertEqual(row["exchange"], "BSE + NSE")


class PartyClubbing(unittest.TestCase):
    def test_repeat_trades_by_one_party_are_clubbed_into_a_single_entry(self):
        row, _ = only_row(
            [
                leg("NSE", BULK, "SELL", 600_000, 1000, "SELLER A"),
                leg("NSE", BULK, "SELL", 400_000, 1000, "SELLER A"),
            ]
        )
        self.assertEqual(row["sellers"], "SELLER A (100.00 cr)")

    def test_parties_are_listed_largest_first(self):
        row, _ = only_row(
            [
                leg("NSE", BULK, "BUY", 100_000, 1000, "SMALL BUYER"),
                leg("NSE", BULK, "BUY", 900_000, 1000, "BIG BUYER"),
            ]
        )
        self.assertEqual(row["buyers"], "BIG BUYER (90.00 cr); SMALL BUYER (10.00 cr)")


class RowPresentation(unittest.TestCase):
    def test_single_price_is_shown_plainly_and_mixed_prices_as_a_range(self):
        single, _ = only_row([leg("NSE", BULK, "SELL", 1_000_000, 1000, "SELLER A")])
        self.assertEqual(single["price"], "1,000.00")

        mixed, _ = only_row(
            [
                leg("NSE", BULK, "SELL", 500_000, 1000, "SELLER A"),
                leg("NSE", BULK, "SELL", 500_000, 1010.5, "SELLER B"),
            ]
        )
        self.assertEqual(mixed["price"], "1,000.00 - 1,010.50")

    def test_year_is_derived_from_the_trade_date(self):
        row, _ = only_row([leg("NSE", BULK, "SELL", 1_000_000, 1000, "SELLER A")])
        self.assertEqual(row["year"], DAY.year)
        self.assertEqual(row["trade_date"], "30-Jun-2026")

    def test_aggregation_does_not_mutate_its_input(self):
        legs = [
            leg("NSE", BLOCK, "SELL", 1_000_000, 1000, "SELLER A"),
            leg("NSE", BULK, "SELL", 1_000_000, 1000, "SELLER A"),
        ]
        before = [(item.deal_type, item.quantity) for item in legs]
        first = build_rows(legs, 0)[0]
        second = build_rows(legs, 0)[0]
        self.assertEqual(before, [(item.deal_type, item.quantity) for item in legs])
        self.assertEqual(first, second)

    def test_rows_are_separate_per_company_and_date(self):
        rows, _ = build_rows(
            [
                leg("NSE", BULK, "SELL", 1_000_000, 1000, "SELLER A", symbol="AAA"),
                leg("NSE", BULK, "SELL", 1_000_000, 1000, "SELLER A", symbol="BBB"),
            ],
            0,
        )
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
