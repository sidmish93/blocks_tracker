"""Audit helper: show the raw NSE/BSE legs behind one tracker row.

Use this to cross-check a number in the tracker against what the exchange
websites actually published.

    python verify_deal.py BIOCON 2026-07-14
"""

import sys
from datetime import date

from app.aggregate import build_rows
from app.normalize import canonical
from app.sources import fetch_all


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    needle = sys.argv[1].upper()
    target = date.fromisoformat(sys.argv[2])

    legs, errors = fetch_all(target, target)
    if errors:
        print("warnings:", errors)

    picked = [
        leg
        for leg in legs
        if needle in leg.symbol.upper() or needle in canonical(leg.security_name)
    ]
    print(f"RAW LEGS  {needle}  {target}   ({len(picked)} legs)\n")
    header = f"{'EX':4} {'TYPE':6} {'SIDE':5} {'QUANTITY':>14} {'PRICE':>10} {'VALUE cr':>10}  CLIENT"
    print(header)
    print("-" * len(header))
    for leg in sorted(picked, key=lambda item: (item.exchange, item.deal_type, item.side, -item.quantity)):
        print(
            f"{leg.exchange:4} {leg.deal_type:6} {leg.side:5} {leg.quantity:14,.0f} "
            f"{leg.price:10,.2f} {leg.value / 1e7:10,.2f}  {leg.client}"
        )

    print("\nFEED TOTALS")
    for exchange in ("NSE", "BSE"):
        for deal_type in ("Bulk", "Block"):
            for side in ("BUY", "SELL"):
                subset = [
                    leg
                    for leg in picked
                    if leg.exchange == exchange and leg.deal_type == deal_type and leg.side == side
                ]
                if subset:
                    print(
                        f"  {exchange} {deal_type:5} {side:4}  qty={sum(l.quantity for l in subset):15,.0f}"
                        f"  value={sum(l.value for l in subset) / 1e7:10,.2f} cr"
                    )

    rows, _ = build_rows(legs, 0)
    matches = [
        row
        for row in rows
        if needle in row["ticker"].upper() or needle in canonical(row["company_name"])
    ]
    print("\nTRACKER ROW")
    if not matches:
        print("  (no row produced - every party may have been excluded as a self-trade)")
    for row in matches:
        for key, value in row.items():
            if key != "trade_date_iso":
                print(f"  {key:14} {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
