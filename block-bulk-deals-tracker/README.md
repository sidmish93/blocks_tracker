# Block & Bulk Deals Tracker

A small web app that pulls bulk and block deal disclosures from **NSE** and **BSE**, merges
them into one deal per company per trade date, and gives you the result on screen and as a
downloadable Excel file.

## Running it

Double-click **`start.bat`**, or from a terminal:

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8765
```

Then open <http://127.0.0.1:8765/>.

Set a **minimum deal size in crores**, choose a period, press **Generate tracker**, and the
table appears. **Download Excel** saves the same table as `.xlsx`.

### Searching by company

Leave **Company** blank to scan the whole market, or name one company, or name several
separated by semicolons:

```
Delhivery; Lodha Developers; Vedanta
```

The names do not have to be exact — "Delhivery", "Delhivery Ltd", "Delhivery Limited" and
"Delhivery Pvt Ltd" all find the same company, and suggestions appear as you type,
following only the name the cursor is in. NSE only accepts a symbol, so the app resolves
whatever you type to the right symbol and scrip code first.

Names that match nothing are all reported at once, since a typo has to be retyped anyway.
A name matching several companies (say "Bajaj" or "Tata Motors") brings up a chooser
showing tickers and listing dates, and picking one settles that name and leaves the others
alone. Repeating the same company under different spellings searches it once.

Companies are listed alphabetically in the result, whatever order you typed them in, so the
deals, market data and news all read the same way.

### Choosing a period

Two options, side by side:

- **Custom range** — your own From and To dates, applied to every company. Works with or
  without a company.
- **Since listing** — one click, from each company's IPO listing date to today. Needs at
  least one company. **Every company gets its own start date**, so searching Delhivery and
  MCX together covers Delhivery from May 2022 and MCX from March 2012. The summary reports
  the widest of those spans. The listing date comes from NSE's master; where none is
  published the search covers all available history instead.

Naming several companies costs little: they are fetched in parallel, so three companies
since listing take about as long as one.

## Market data

Alongside the deals, every company in the result gets a row of trailing market data,
computed from NSE's daily price/volume/delivery archive and BSE's live market
capitalisation. It appears above the deals table and as a second **Market Data** sheet in
the Excel file.

| Metric | How it is calculated |
| --- | --- |
| Return 1D / 1W / 1M | Latest close against the close one session, 7 calendar days and 30 calendar days earlier |
| ADTV 1D / 1W / 1M | Total traded value over the window, divided by the number of sessions in it |
| VWAP 1D / 1W / 1M | Total traded value over the window, divided by total traded quantity |
| Delivery 1D / 1W / 1M | Total delivered quantity over the window, divided by total traded quantity |
| Market Cap | BSE full market capitalisation, live |

These are **current snapshots**. They are measured from the latest session the security
traded in, so they are the same whether you chose Since listing or a custom range — the
period only controls which deals are listed.

Windows are calendar-based, so "1 month" means against the price a month ago rather than
21 sessions ago. Where a window straddles a weekend or holiday, the reference close is the
last session on or before that date.

**Prices are adjusted for splits and bonuses.** Closes and volumes before an ex-date are
restated in terms of today's share count, using NSE's corporate actions feed. Without
this, a 5-for-1 split inside the window would read as an 80% crash. Dividends are not
adjusted for, since these are price returns rather than total returns; rights issues are
left alone because adjusting them needs the subscription terms.

Two things can legitimately come back blank. Delivery is blank for securities in NSE's
trade-for-trade series, where NSE publishes no deliverable data at all — that is not the
same as nothing having been delivered. Traded stats are blank for the few BSE-only
securities (mostly InvITs), and market cap is blank for the few NSE-only ones. The
interface says which company and why.

## News

Every company in the result also gets its recent coverage, from the last three days, drawn
only from CNBC TV18, Moneycontrol, NDTV Profit, Economic Times, Business Standard and Mint.
Each story shows its source, publication time and headline, linking straight to the
publisher. It sits between the market data and the deals, and forms a third **News** sheet
in the Excel file where both the headline and the link cells are clickable.

Like the market data, this is a current snapshot: the three-day window is measured from
today and does not move with the deal period you chose.

Each of the six sites is searched separately, so a prolific publisher cannot crowd out the
rest. Two filters then do the real work:

**The headline has to name the company.** A search engine matches body text as well as
headlines, so a query for one company routinely returns stories about another — searching
Delhivery returns pieces on Zepto, Pine Labs and Airtel that merely mention it in passing.
Only stories naming the company in the headline are kept. Matching allows for the names a
headline would actually use: the registered name minus its corporate suffix, a two-word
form, and the ticker. The two-word form matters for family groups, since matching on
"Adani" alone would attribute every group story to all of Adani Energy, Adani Enterprises
and Adani Green.

**Stock-quote and topic pages are discarded.** Search results are littered with pages like
`business-standard.com/markets/delhivery-ltd-share-price-68151.html` that carry today's
date but are not news. These are recognised by their URL shape, carefully enough to keep
genuine articles whose slug happens to contain the same words.

The stories that survive are ranked by relevance rather than time, and the top 15 per
company are kept. A headline that opens with the company outranks one that mentions it at
the end; a focused story outranks a roundup listing a dozen tickers; fresher outranks
older; and a match on the ticker alone ranks below a match on the name, because a ticker
can name something else — MCX is a commodities exchange as well as a listed company, so
"MCX gold slips" sits below news about the firm itself.

A company with no coverage in the window simply has no rows, which is common: most
companies are not in the news on a given day.

## Columns

| Column | Meaning |
| --- | --- |
| Company Name | Resolved from the exchange scrip masters, so NSE and BSE spellings agree |
| Trade Date | Date of the deal (a real date in Excel, so it sorts and pivots) |
| Year | Taken from the trade date |
| Ticker | NSE symbol where the company is listed on NSE, otherwise the BSE scrip ID |
| Quantity Traded | Shares on the larger disclosed side of the deal |
| Trade Price / Wtd. Avg. Price | A single price, or a `low - high` range when the deal filled at several prices |
| Deal Size (Rs cr) | Value of the larger disclosed side |
| Type | `Bulk`, `Block`, `Both Block and Bulk`, or a combination |
| Sellers | Each seller with their share of the deal, largest first, separated by `;` |
| Buyers | Same, for the buy side |
| Exchange | `NSE`, `BSE`, or `BSE + NSE` |

## The rules it applies

**One row per company per trade date.** NSE and BSE activity in the same company on the
same day is merged into a single row. Companies are matched across the two exchanges by
ISIN, falling back to a normalised company name for the handful of scrips (rights
entitlements, partly-paid shares) that the masters do not carry.

**Buyers and sellers are clubbed.** If the same party appears several times for a company
on a date, its quantities and values are added together and shown once. Parties are
matched after normalising punctuation and abbreviations, so `MORGAN STANLEY ASIA
(SINGAPORE) PTE.` and `MORGAN STANLEY ASIA SINGAPORE PTE` are treated as one entity.

**Self-trades are excluded.** When a party appears as both buyer and seller in the same
company on the same date it is almost always an HFT or proprietary desk, so all of its
legs are removed. Other parties in that same company and date are unaffected, so a day
that mixes genuine and prop activity keeps the genuine part. In practice this removes
names such as HRTI, Jump Trading, iRage and AlphaGrep.

**Bulk and block disclosures of the same trade are counted once.** Large block deals are
usually published in both feeds. An exchange's bulk feed reports a client's whole day in a
single row, while its block feed itemises the individual trades, so matching is done on
the client's total quantity per side rather than row by row. Where the two agree the
shares are counted once and tagged `Both Block and Bulk`; where the bulk total is larger,
the excess is added on top as `Bulk`. Deduplication never crosses exchanges, because the
same shares cannot trade on both.

**Deal size uses the larger disclosed side.** Disclosure is per client crossing a
threshold, so a deal can have a fully disclosed sell side and only a partial buy side.
Taking the larger side avoids understating the deal. This is also why the buyer amounts
and seller amounts in a row often do not tie out to each other.

**The size filter applies to the row.** A company and date qualifies when its total deal
size reaches your threshold, not when a single trade does.

## Data sources

| Purpose | Endpoint |
| --- | --- |
| NSE deals | `nseindia.com/api/historicalOR/bulk-block-short-deals` (CSV mode) |
| BSE deals | `api.bseindia.com/BseIndiaAPI/api/BulkDealData_ng/w` |
| NSE names, ISINs, listing dates | `EQUITY_L.csv`, `SME_EQUITY_L.csv`, `eq_etfseclist.csv` |
| BSE names & ISINs | `api.bseindia.com/BseIndiaAPI/api/ListofScripData/w` |
| NSE daily price, volume, delivery | `nseindia.com/api/historicalOR/generateSecurityWiseHistoricalData` |
| NSE splits and bonuses | `nseindia.com/api/corporates-corporateActions` |
| BSE market cap | `api.bseindia.com/BseIndiaAPI/api/StockTrading/w` |
| Company news | `bing.com/news/search` in RSS mode, one query per publisher |

NSE rejects ranges wider than about a year, so requests are split into whole calendar
years. Whole years are used rather than exact windows so that the cache key does not move
as "today" advances and any two overlapping queries reuse the same downloads. Both feeds
can be filtered to a single company, which is what makes a since-listing search quick —
thirty years of one company takes a few seconds.

NSE's bulk and block archive begins in January 2005. For a company listed before then,
"Since listing" covers 2005 onward on the NSE side.

Market data is fetched one company at a time, so the companies in a result are fetched in
parallel; a month of market-wide deals covering around thirty companies adds a few seconds.
NSE's history is tried against the ordinary equity series first, then the trade-for-trade,
SME and InvIT segments, so securities outside the main board still resolve. NSE's own quote
endpoint is behind a bot filter and returns 403, which is why market cap comes from BSE.

News comes from a news-search index rather than the publishers' own feeds. Their feeds
carry only the current front page, which is far too little to cover three days of one
company, and Moneycontrol's has not updated since 2024. The other obvious index, Google
News, has better recall but publishes only its own redirect links — the article URL is
nowhere in the feed — so it cannot give you a link to the publisher. This one exposes the
real URL inside its redirect. The six companies' worth of queries per company run in
parallel across companies; a thirty-company result adds around seven seconds.

Responses are cached in `.cache/` — closed periods for a week, the current period and all
market data for 30 minutes, news searches for 15 minutes. Delete the folder to force a
refresh.

Requests use `curl_cffi` with a browser TLS fingerprint, because NSE sits behind a bot
filter that rejects ordinary HTTP clients.

## Checking a number

To see the raw exchange rows behind any tracker row:

```bash
python verify_deal.py BIOCON 2026-07-14
```

It prints every leg both exchanges published that day, the per-feed totals, and the
tracker row built from them.

## Tests

```bash
python -m unittest discover -s tests
```

These cover the aggregation rules, company name matching, the multi-company request
handling (splitting names, resolving them, and giving each its own window), the market data
maths (window selection, VWAP, delivery, and split/bonus adjustment) and the news filtering
(aliasing, headline matching, quote-page rejection and ranking), all with synthetic data,
and need no network access.
