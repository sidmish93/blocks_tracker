"""Tests for turning a typed company name into a tradeable symbol (no network)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import companies  # noqa: E402
from app.companies import Company, _finalise, _searchable  # noqa: E402


def make(name, nse="", bse_code="", bse_ticker="", isin="", listing=""):
    company = Company(
        key=isin or name,
        name=name,
        isin=isin,
        nse_symbol=nse,
        bse_code=bse_code,
        bse_ticker=bse_ticker,
        listing_date=listing,
    )
    _finalise(company)
    return company


FIXTURES = [
    make("Delhivery Limited", nse="DELHIVERY", bse_code="543529", bse_ticker="DELHIVERY",
         isin="INE148O01028", listing="2022-05-24"),
    make("Biocon Limited", nse="BIOCON", bse_code="532523", isin="INE376G01013",
         listing="2004-04-07"),
    make("Reliance Industries Limited", nse="RELIANCE", bse_code="500325",
         isin="INE002A01018", listing="1995-11-29"),
    make("Reliance Industries Ltd", bse_code="890147", bse_ticker="RELIANCEPP",
         isin="IN9002A01032"),
    make("Bajaj Auto Limited", nse="BAJAJ-AUTO", bse_code="532977", isin="INE917I01010"),
    make("Bajaj Finserv Limited", nse="BAJAJFINSV", bse_code="532978", isin="INE918I01026"),
    make("Multi Commodity Exchange of India Limited", nse="MCX", bse_code="534091",
         isin="INE745G01035", listing="2012-03-09"),
]


class CompanyResolution(unittest.TestCase):
    def setUp(self):
        companies._index = {company.key: company for company in FIXTURES}
        companies._extended_loaded = True

    def tearDown(self):
        companies._index = None
        companies._extended_loaded = False

    def test_every_spelling_of_a_name_finds_the_same_company(self):
        spellings = [
            "Delhivery",
            "Delhivery Limited",
            "Delhivery Ltd",
            "Delhivery Pvt Ltd",
            "delhivery pvt. ltd.",
            "  DELHIVERY  ",
        ]
        for spelling in spellings:
            with self.subTest(spelling=spelling):
                company, _ = companies.resolve(spelling)
                self.assertIsNotNone(company, f"{spelling!r} did not resolve")
                self.assertEqual(company.nse_symbol, "DELHIVERY")

    def test_a_ticker_resolves_directly(self):
        company, _ = companies.resolve("MCX")
        self.assertEqual(company.nse_symbol, "MCX")

    def test_the_listing_date_comes_through(self):
        company, _ = companies.resolve("Delhivery")
        self.assertEqual(company.listing_day().isoformat(), "2022-05-24")

    def test_a_partial_name_matching_several_companies_asks_the_user(self):
        company, alternatives = companies.resolve("Bajaj")
        self.assertIsNone(company)
        names = {item.name for item in alternatives}
        self.assertIn("Bajaj Auto Limited", names)
        self.assertIn("Bajaj Finserv Limited", names)

    def test_a_single_partial_match_is_accepted_without_asking(self):
        company, alternatives = companies.resolve("Multi Commodity Exchange")
        self.assertIsNotNone(company)
        self.assertEqual(company.nse_symbol, "MCX")
        self.assertEqual(alternatives, [])

    def test_the_ordinary_nse_line_wins_over_a_partly_paid_twin(self):
        company, _ = companies.resolve("Reliance Industries")
        self.assertIsNotNone(company)
        self.assertEqual(company.nse_symbol, "RELIANCE")

    def test_a_typo_suggests_candidates_rather_than_failing_silently(self):
        company, alternatives = companies.resolve("Delhivary")
        self.assertIsNone(company)
        self.assertTrue(alternatives)
        self.assertEqual(alternatives[0].nse_symbol, "DELHIVERY")

    def test_nonsense_finds_nothing(self):
        company, alternatives = companies.resolve("qwertyuiop asdfgh")
        self.assertIsNone(company)
        self.assertEqual(alternatives, [])

    def test_search_returns_a_ranked_shortlist(self):
        results = companies.search("Bajaj")
        self.assertGreaterEqual(len(results), 2)
        self.assertTrue(all("Bajaj" in item.name for item in results))


class IndexFiltering(unittest.TestCase):
    def test_rights_entitlements_are_kept_out_of_the_index(self):
        # BSE 75xxxx codes are short-lived rights lines named after their ticker.
        self.assertFalse(_searchable("750382", {"name": "BAJAJFINLR", "ticker": "BAJAJFINLR"}))
        self.assertFalse(_searchable("750566", {"name": "Tata Motors Ltd", "ticker": "TATAMOTORLR"}))

    def test_real_companies_are_kept(self):
        self.assertTrue(
            _searchable("500325", {"name": "Reliance Industries Ltd", "ticker": "RELIANCE",
                                   "isin": "INE002A01018"})
        )

    def test_entries_with_no_name_are_dropped(self):
        self.assertFalse(_searchable("512345", {"name": "", "ticker": "SOMETHING"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
