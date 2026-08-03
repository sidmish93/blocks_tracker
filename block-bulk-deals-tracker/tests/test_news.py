"""Tests for the news matching, filtering and ranking rules (no network)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.news import (  # noqa: E402
    _pattern,
    aliases,
    headline_position,
    is_article,
    relevance,
    short_name,
)


def position(headline, name, ticker=""):
    patterns = [_pattern(alias) for alias in aliases(name, ticker)]
    return headline_position(headline, patterns)


class ShortName(unittest.TestCase):
    def test_corporate_suffixes_are_dropped(self):
        self.assertEqual(short_name("Delhivery Limited"), "Delhivery")
        self.assertEqual(short_name("Shaily Engineering Plastics Ltd."), "Shaily Engineering Plastics")

    def test_inner_words_are_kept(self):
        self.assertEqual(
            short_name("Multi Commodity Exchange of India Limited"),
            "Multi Commodity Exchange of India",
        )

    def test_a_suffix_word_inside_the_name_survives(self):
        self.assertEqual(
            short_name("Container Corporation of India Ltd"), "Container Corporation of India"
        )

    def test_a_leading_the_is_dropped(self):
        self.assertEqual(short_name("The Ramco Cements Limited"), "Ramco Cements")


class Aliases(unittest.TestCase):
    def test_long_names_also_get_a_two_word_form(self):
        self.assertEqual(
            aliases("Adani Energy Solutions Limited", "ADANIENSOL"),
            ["Adani Energy Solutions", "Adani Energy", "ADANIENSOL"],
        )

    def test_short_names_are_not_duplicated(self):
        self.assertEqual(aliases("Delhivery Limited", "DELHIVERY"), ["Delhivery"])

    def test_very_short_tickers_are_not_used_as_aliases(self):
        self.assertEqual(aliases("Shaily Engineering Plastics Ltd", "AB"), ["Shaily Engineering Plastics", "Shaily Engineering"])


class HeadlineMatching(unittest.TestCase):
    def test_company_named_in_the_headline_is_found(self):
        self.assertEqual(position("Delhivery CEO explains the threat", "Delhivery Ltd"), 0)

    def test_company_only_in_the_body_is_not_matched(self):
        self.assertEqual(position("Pine Labs Q1 net profit jumps four-fold", "Delhivery Ltd"), -1)

    def test_sister_companies_are_not_confused(self):
        headline = "Adani Energy to raise Rs 3,500 crore via a QIP"
        self.assertGreaterEqual(position(headline, "Adani Energy Solutions Ltd", "ADANIENSOL"), 0)
        self.assertEqual(position(headline, "Adani Enterprises Ltd", "ADANIENT"), -1)
        self.assertEqual(position(headline, "Adani Green Energy Ltd", "ADANIGREEN"), -1)

    def test_ticker_is_matched_when_the_headline_uses_it(self):
        self.assertGreaterEqual(
            position("M&M moves truck division to SML Mahindra", "Mahindra & Mahindra Ltd", "M&M"), 0
        )

    def test_a_longer_word_containing_the_alias_does_not_count(self):
        self.assertEqual(
            position("Coronavirus cases climb again", "CORONA Remedies Ltd", "CORONA"), -1
        )

    def test_match_position_is_where_the_company_first_appears(self):
        headline = "Buy, Sell Or Hold: Swiggy, Delhivery, Raymond"
        self.assertEqual(position(headline, "Delhivery Ltd"), headline.index("Delhivery"))


class JunkFiltering(unittest.TestCase):
    def test_quote_pages_are_rejected(self):
        cases = [
            ("https://www.business-standard.com/markets/delhivery-ltd-share-price-68151.html", "Delhivery Share Price"),
            ("https://www.cnbctv18.com/market/stocks/adani-energy-solutions-ltd-share-price/AT22/", "Adani Energy Solutions Ltd."),
            ("https://www.livemint.com/market/market-stats/delhivery-q4-results-s0005265", "Delhivery Q4 Results"),
            ("https://economictimes.indiatimes.com/delhivery-ltd/stockreports/reportid-2041701.cms", "Stock Research Report for Delhivery Ltd"),
        ]
        for url, headline in cases:
            with self.subTest(url=url):
                self.assertFalse(is_article(url, headline))

    def test_topic_and_reference_hubs_are_rejected(self):
        self.assertFalse(is_article("https://www.business-standard.com/topic/ekart-logistics", "Ekart Logistics"))
        self.assertFalse(
            is_article("https://www.moneycontrol.com/company-facts/multicommodityexchangeindia/bonus/MCE/", "Multi Commodity Exchange of India")
        )
        self.assertFalse(
            is_article("https://www.moneycontrol.com/technical-analysis/vwap/vedantaoilgas/VOGL", "Vedanta Oil and Gas")
        )

    def test_real_articles_whose_slug_mentions_share_price_are_kept(self):
        cases = [
            ("https://www.business-standard.com/markets/news/lodha-developers-share-price-zooms-99-from-april-low-hits-high", "Lodha Developers zooms 99% from April low, hits 11-month high"),
            ("https://www.cnbctv18.com/market/stocks/vedanta-power-share-price-q1-results-revenue-dips-3", "Vedanta Power Q1 Results: Revenue dips 3%, exceptional loss pushes company into loss"),
            ("https://www.moneycontrol.com/news/business/stocks/lodha-developers-jumps-5-after-q1-earnings", "Lodha Developers jumps 5% after Q1 earnings; Nomura retains 'Buy'"),
        ]
        for url, headline in cases:
            with self.subTest(url=url):
                self.assertTrue(is_article(url, headline))


class Ranking(unittest.TestCase):
    def test_a_headline_led_by_the_company_beats_a_passing_mention(self):
        led = relevance("Delhivery CEO explains the threat", 0, 0.5)
        passing = relevance("PNB Housing tops the list; Crompton, Delhivery make the cut", 48, 0.5)
        self.assertGreater(led, passing)

    def test_roundups_rank_below_focused_stories(self):
        focused = relevance("Lodha Developers bets bigger on data centre land sales", 0, 1.0)
        roundup = relevance("Stock Alert: IDFC First, Cyient, Lodha Developers, BoB, Dodla", 0, 1.0)
        self.assertGreater(focused, roundup)

    def test_fresher_stories_rank_higher_all_else_equal(self):
        self.assertGreater(relevance("Lodha wins big", 0, 0.2), relevance("Lodha wins big", 0, 2.6))

    def test_a_ticker_only_match_ranks_below_a_name_match(self):
        by_name = relevance("Gold slips on the exchange", 0, 1.0, by_ticker=False)
        by_ticker = relevance("Gold slips on the exchange", 0, 1.0, by_ticker=True)
        self.assertGreater(by_name, by_ticker)


if __name__ == "__main__":
    unittest.main()
