"""Tests for turning a request into companies and their search windows (no network)."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402

from app import companies  # noqa: E402
from app.config import BSE_DATA_START  # noqa: E402
from app.main import (  # noqa: E402
    TrackerRequest,
    _filename,
    _plans,
    _resolve_companies,
    split_terms,
)
from tests.test_companies import FIXTURES  # noqa: E402


class TermSplitting(unittest.TestCase):
    def test_a_single_name_is_one_term(self):
        self.assertEqual(split_terms("Delhivery"), ["Delhivery"])

    def test_names_are_split_on_semicolons_and_trimmed(self):
        self.assertEqual(
            split_terms(" Delhivery ;  Lodha Developers;Vedanta "),
            ["Delhivery", "Lodha Developers", "Vedanta"],
        )

    def test_stray_separators_do_not_create_blank_terms(self):
        self.assertEqual(split_terms(";; Delhivery ;;; Vedanta ;"), ["Delhivery", "Vedanta"])

    def test_the_same_name_twice_is_searched_once(self):
        self.assertEqual(split_terms("Delhivery; delhivery ; DELHIVERY"), ["Delhivery"])

    def test_nothing_typed_is_no_terms(self):
        self.assertEqual(split_terms(""), [])
        self.assertEqual(split_terms(None), [])
        self.assertEqual(split_terms("   ;  "), [])


class Resolution(unittest.TestCase):
    def setUp(self):
        companies._index = {company.key: company for company in FIXTURES}
        companies._extended_loaded = True

    def tearDown(self):
        companies._index = None
        companies._extended_loaded = False

    def request(self, **kwargs):
        kwargs.setdefault("from_date", date(2026, 6, 1))
        kwargs.setdefault("to_date", date(2026, 6, 30))
        return TrackerRequest(**kwargs)

    def test_no_company_means_the_whole_market(self):
        self.assertEqual(_resolve_companies(self.request()), [])

    def test_several_names_resolve_together(self):
        found = _resolve_companies(self.request(company="Delhivery; Biocon; MCX"))
        self.assertEqual({item.nse_symbol for item in found}, {"DELHIVERY", "BIOCON", "MCX"})

    def test_companies_come_back_alphabetically_whatever_the_typed_order(self):
        # The market data and news tables are alphabetical, so this one is too.
        for typed in ("Delhivery; Biocon; MCX", "MCX; Delhivery; Biocon"):
            with self.subTest(typed=typed):
                found = _resolve_companies(self.request(company=typed))
                self.assertEqual(
                    [item.nse_symbol for item in found], ["BIOCON", "DELHIVERY", "MCX"]
                )

    def test_spellings_of_the_same_company_collapse_to_one(self):
        found = _resolve_companies(self.request(company="Delhivery Ltd; DELHIVERY; Delhivery"))
        self.assertEqual(len(found), 1)

    def test_pinned_keys_and_typed_names_combine(self):
        found = _resolve_companies(
            self.request(company_keys=["INE148O01028"], company="Biocon")
        )
        self.assertEqual([item.nse_symbol for item in found], ["BIOCON", "DELHIVERY"])

    def test_a_key_and_a_name_for_the_same_company_are_not_searched_twice(self):
        found = _resolve_companies(
            self.request(company_keys=["INE148O01028"], company="Delhivery Ltd")
        )
        self.assertEqual(len(found), 1)

    def test_every_unknown_name_is_reported_at_once(self):
        with self.assertRaises(HTTPException) as caught:
            _resolve_companies(self.request(company="Delhivery; qwertyuiop asdfgh; zxcvbnm qwerty"))
        self.assertEqual(caught.exception.status_code, 404)
        self.assertIn("qwertyuiop asdfgh", caught.exception.detail)
        self.assertIn("zxcvbnm qwerty", caught.exception.detail)

    def test_an_ambiguous_name_says_which_one_it_is(self):
        with self.assertRaises(HTTPException) as caught:
            _resolve_companies(self.request(company="Delhivery; Bajaj"))
        detail = caught.exception.detail
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(detail["term"], "Bajaj")
        self.assertGreaterEqual(len(detail["candidates"]), 2)

    def test_an_unknown_name_is_raised_before_an_ambiguous_one(self):
        # A typo has to be retyped, so there is no point choosing a listing first.
        with self.assertRaises(HTTPException) as caught:
            _resolve_companies(self.request(company="Bajaj; qwertyuiop asdfgh"))
        self.assertEqual(caught.exception.status_code, 404)


class Windows(unittest.TestCase):
    def setUp(self):
        companies._index = {company.key: company for company in FIXTURES}
        companies._extended_loaded = True

    def tearDown(self):
        companies._index = None
        companies._extended_loaded = False

    def chosen(self, names):
        return _resolve_companies(
            TrackerRequest(company=names, from_date=date(2026, 6, 1), to_date=date(2026, 6, 30))
        )

    def test_a_custom_range_is_the_same_for_every_company(self):
        request = TrackerRequest(
            company="Delhivery; Biocon", from_date=date(2026, 6, 1), to_date=date(2026, 6, 30)
        )
        plans = _plans(request, self.chosen("Delhivery; Biocon"))
        self.assertEqual([(plan.start, plan.end) for plan in plans],
                         [(date(2026, 6, 1), date(2026, 6, 30))] * 2)

    def test_since_listing_gives_each_company_its_own_start(self):
        request = TrackerRequest(company="Delhivery; Biocon", since_listing=True)
        plans = _plans(request, self.chosen("Delhivery; Biocon"))
        starts = {plan.company.nse_symbol: plan.start for plan in plans}
        self.assertEqual(starts, {"BIOCON": date(2004, 4, 7), "DELHIVERY": date(2022, 5, 24)})
        self.assertEqual({plan.end for plan in plans}, {date.today()})

    def test_a_company_with_no_published_listing_date_searches_all_history(self):
        request = TrackerRequest(company="Bajaj Auto", since_listing=True)
        plans = _plans(request, self.chosen("Bajaj Auto"))
        self.assertEqual(plans[0].start, BSE_DATA_START)

    def test_the_whole_market_is_one_plan_with_no_company(self):
        request = TrackerRequest(from_date=date(2026, 6, 1), to_date=date(2026, 6, 30))
        plans = _plans(request, [])
        self.assertEqual(len(plans), 1)
        self.assertIsNone(plans[0].company)


class Validation(unittest.TestCase):
    def test_since_listing_needs_a_company(self):
        with self.assertRaises(ValueError):
            TrackerRequest(since_listing=True)

    def test_a_custom_range_needs_both_dates(self):
        with self.assertRaises(ValueError):
            TrackerRequest(from_date=date(2026, 6, 1))

    def test_the_range_cannot_run_backwards(self):
        with self.assertRaises(ValueError):
            TrackerRequest(from_date=date(2026, 6, 30), to_date=date(2026, 6, 1))


class Filenames(unittest.TestCase):
    def setUp(self):
        companies._index = {company.key: company for company in FIXTURES}
        companies._extended_loaded = True

    def tearDown(self):
        companies._index = None
        companies._extended_loaded = False

    def named(self, names):
        return _resolve_companies(
            TrackerRequest(company=names, from_date=date(2026, 6, 1), to_date=date(2026, 6, 30))
        )

    def test_the_whole_market_is_unnamed(self):
        name = _filename([], date(2026, 6, 1), date(2026, 6, 30), 200)
        self.assertEqual(name, "block-bulk-deals_20260601-20260630_min200cr.xlsx")

    def test_a_few_companies_are_listed_by_ticker(self):
        name = _filename(self.named("Delhivery; Biocon"), date(2026, 6, 1), date(2026, 6, 30), 200)
        self.assertTrue(name.startswith("biocon-delhivery_block-bulk-deals_"), name)

    def test_many_companies_are_counted_instead_of_listed(self):
        chosen = self.named("Delhivery; Biocon; MCX; Bajaj Auto")
        name = _filename(chosen, date(2026, 6, 1), date(2026, 6, 30), 200)
        self.assertTrue(name.startswith("4-companies_block-bulk-deals_"))

    def test_punctuated_tickers_stay_filename_safe(self):
        chosen = self.named("Bajaj Auto")
        name = _filename(chosen, date(2026, 6, 1), date(2026, 6, 30), 200)
        self.assertTrue(name.startswith("bajaj-auto_block-bulk-deals_"))


if __name__ == "__main__":
    unittest.main()
