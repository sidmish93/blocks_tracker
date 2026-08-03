from collections import defaultdict

from .config import CRORE
from .normalize import canonical, canonical_company, title_case_company
from .refdata import bse_lookup, nse_lookup
from .sources import BLOCK, BULK

BOTH = "Both Block and Bulk"
TYPE_ORDER = {BULK: 0, BLOCK: 1, BOTH: 2}

# A bulk row covering the same shares as the block rows rarely matches to the
# last share, so treat a tiny leftover as nothing.
RESIDUAL_TOLERANCE = 0.001


class Security:
    """Identity of a traded company, resolved across both exchanges."""

    def __init__(self, isin):
        self.isin = isin
        self.nse_symbol = ""
        self.bse_ticker = ""
        self.bse_code = ""
        self.names = defaultdict(int)

    def observe(self, exchange, symbol, name, scrip_code=""):
        if exchange == "NSE" and symbol:
            self.nse_symbol = symbol
        if exchange == "BSE" and symbol:
            self.bse_ticker = symbol
        if scrip_code:
            self.bse_code = scrip_code
        if name:
            self.names[name] += 1

    @property
    def ticker(self):
        return self.nse_symbol or self.bse_ticker

    @property
    def display_name(self):
        if not self.names:
            return self.ticker
        best = max(self.names.items(), key=lambda item: (item[1], len(item[0])))[0]
        return title_case_company(best)


def _identify(leg):
    """Resolve a leg to (grouping key, isin, ticker, company name)."""
    if leg.exchange == "NSE":
        reference = nse_lookup(leg.symbol)
        isin = reference.get("isin", "")
        name = reference.get("name") or leg.security_name or leg.symbol
        ticker = leg.symbol
    else:
        reference = bse_lookup(leg.scrip_code)
        isin = reference.get("isin", "")
        name = reference.get("name") or leg.security_name or leg.symbol
        ticker = reference.get("ticker") or leg.symbol

    key = isin or canonical_company(name) or ticker
    return key, isin, ticker, name


class _PartyFeeds:
    """Everything one counterparty did on one side, on one exchange, on one day,
    kept split by the feed that reported it."""

    def __init__(self, name):
        self.name = name
        self.totals = {BULK: [0.0, 0.0], BLOCK: [0.0, 0.0]}  # deal type -> [quantity, value]
        self.prices = {BULK: [], BLOCK: []}

    def add(self, leg):
        totals = self.totals[leg.deal_type]
        totals[0] += leg.quantity
        totals[1] += leg.value
        self.prices[leg.deal_type].append(leg.price)
        if len(leg.client.strip()) > len(self.name):
            self.name = leg.client.strip()

    def contributions(self):
        """Reconcile the two feeds into non-overlapping contributions.

        An exchange's bulk feed reports a client's whole day in one row, while the
        block feed itemises the individual block trades. When both describe the
        same client they describe the same shares, so the block quantity is
        counted once as "Both Block and Bulk" and only the excess bulk quantity
        is added on top.
        """
        bulk_qty, bulk_value = self.totals[BULK]
        block_qty, block_value = self.totals[BLOCK]

        if block_qty <= 0:
            if bulk_qty <= 0:
                return []
            return [(BULK, bulk_qty, bulk_value, self.prices[BULK])]
        if bulk_qty <= 0:
            return [(BLOCK, block_qty, block_value, self.prices[BLOCK])]

        results = [(BOTH, block_qty, block_value, self.prices[BLOCK])]
        residual_qty = bulk_qty - block_qty
        if residual_qty >= 1 and residual_qty > RESIDUAL_TOLERANCE * bulk_qty:
            residual_value = bulk_value - block_value
            if residual_value <= 0:
                residual_value = residual_qty * (bulk_value / bulk_qty)
            results.append(
                (BULK, residual_qty, residual_value, [residual_value / residual_qty])
            )
        return results


def _format_parties(parties):
    ordered = sorted(parties, key=lambda party: -party["value"])
    return "; ".join(f"{party['name']} ({party['value'] / CRORE:,.2f} cr)" for party in ordered)


def _format_price(prices):
    low, high = min(prices), max(prices)
    if abs(high - low) < 0.005:
        return f"{low:,.2f}"
    return f"{low:,.2f} - {high:,.2f}"


def build_rows(legs, min_deal_size_cr: float):
    securities = {}
    groups = defaultdict(dict)

    for leg in legs:
        if leg.quantity <= 0 or leg.price <= 0:
            continue
        key, isin, ticker, name = _identify(leg)
        security = securities.setdefault(key, Security(isin))
        security.observe(leg.exchange, ticker, name, leg.scrip_code)

        party_map = groups[(key, leg.trade_date)]
        party_key = (leg.exchange, canonical(leg.client), leg.side)
        if party_key not in party_map:
            party_map[party_key] = _PartyFeeds(leg.client.strip())
        party_map[party_key].add(leg)

    threshold = min_deal_size_cr * CRORE
    rows = []
    self_trade_entities = 0

    for (key, trade_date), party_map in groups.items():
        # Which clients sat on both sides of this company on this day.
        sides_seen = defaultdict(set)
        for exchange, client, side in party_map:
            sides_seen[client].add(side)
        self_traders = {client for client, sides in sides_seen.items() if len(sides) > 1}
        self_trade_entities += len(self_traders)

        clubbed = {}
        deal_types = set()
        exchanges = set()
        prices = []

        for (exchange, client, side), feeds in party_map.items():
            if client in self_traders:
                continue
            for deal_type, quantity, value, leg_prices in feeds.contributions():
                if quantity <= 0 or value <= 0:
                    continue
                deal_types.add(deal_type)
                exchanges.add(exchange)
                prices.extend(leg_prices)
                party = clubbed.setdefault(
                    (side, client),
                    {"name": feeds.name, "side": side, "quantity": 0.0, "value": 0.0},
                )
                party["quantity"] += quantity
                party["value"] += value
                if len(feeds.name) > len(party["name"]):
                    party["name"] = feeds.name

        if not clubbed:
            continue

        buyers = [party for party in clubbed.values() if party["side"] == "BUY"]
        sellers = [party for party in clubbed.values() if party["side"] == "SELL"]
        buy_value = sum(party["value"] for party in buyers)
        sell_value = sum(party["value"] for party in sellers)

        # Only one side of a deal is disclosed when the other side stays below the
        # reporting threshold, so the larger side represents the deal.
        deal_value = max(buy_value, sell_value)
        if deal_value < threshold:
            continue

        security = securities[key]
        rows.append(
            {
                "company_name": security.display_name,
                "trade_date": trade_date.strftime("%d-%b-%Y"),
                "trade_date_iso": trade_date.isoformat(),
                "year": trade_date.year,
                "ticker": security.ticker,
                "quantity": int(
                    round(
                        max(
                            sum(party["quantity"] for party in buyers),
                            sum(party["quantity"] for party in sellers),
                        )
                    )
                ),
                "price": _format_price(prices),
                "deal_size_cr": round(deal_value / CRORE, 2),
                "deal_type": "; ".join(sorted(deal_types, key=lambda item: TYPE_ORDER[item])),
                "sellers": _format_parties(sellers),
                "buyers": _format_parties(buyers),
                "exchange": " + ".join(sorted(exchanges)),
                # Identity, so market data can be joined back to the company.
                "security_key": key,
                "isin": security.isin,
                "nse_symbol": security.nse_symbol,
                "bse_code": security.bse_code,
            }
        )

    rows.sort(key=lambda row: (row["trade_date_iso"], row["deal_size_cr"]), reverse=True)
    return rows, {"self_trade_entities_excluded": self_trade_entities}
