import sys

import pandas as pd

from .. import api
from ..utils import parse_date


def setup_parser(subparsers):
    parser = subparsers.add_parser("history", help="Fetch history and validate")
    parser.add_argument("--isin", help="ISIN of the security")
    parser.add_argument("--symbol", help="Symbol")
    parser.add_argument("--desc", help="Description")
    parser.add_argument("--exchange", help="Preferred exchange")
    parser.add_argument("--period", default="1mo", help="Period (e.g. 1mo, 1y)")
    parser.add_argument("--price", type=float, help="Validation price")
    parser.add_argument("--date", help="Validation date")
    parser.set_defaults(func=handle)


def handle(args):
    preferred = [args.exchange] if args.exchange else None

    ticker_str = api.resolve_ticker(
        isin=args.isin,
        symbol=args.symbol,
        description=args.desc,
        preferred_exchanges=preferred,
        target_price=args.price,
        target_date=args.date,
    )

    if not ticker_str:
        print("Could not resolve ticker.", file=sys.stderr)
        sys.exit(1)

    print(f"Resolved to: {ticker_str}")
    hist = api.history(ticker_str, period=args.period)
    df = hist.to_pandas()

    if df.empty:
        print("No history found.", file=sys.stderr)
        sys.exit(1)

    print(df.tail())

    if args.price and args.date:
        target_dt = parse_date(args.date)
        if not target_dt:
            print(f"Invalid date: {args.date}", file=sys.stderr)
            sys.exit(1)

        target_ts = pd.Timestamp(target_dt)
        row = None
        if target_ts in df.index:
            row = df.loc[target_ts]
        else:
            idx = df.index.get_indexer([target_ts], method="nearest")[0]
            if idx != -1:
                actual_ts = df.index[idx]
                if abs((actual_ts - target_ts).days) <= 3:
                    row = df.iloc[idx]
                    print(f"Using nearest data from {actual_ts.date()}")

        if row is None:
            print(f"Date {args.date} not found in history.", file=sys.stderr)
            sys.exit(1)

        high, low, close_ = row.get("High"), row.get("Low"), row.get("Close")
        passed = False
        if pd.notna(high) and pd.notna(low):
            passed = low <= args.price <= high
        elif pd.notna(close_):
            passed = (abs(close_ - args.price) / args.price) < 0.05

        if passed:
            print("VALIDATION PASSED")
        else:
            msg = (
                f"VALIDATION FAILED (Open={row.get('Open')}, "
                f"High={high}, Low={low}, Close={close_})"
            )
            print(msg)
            sys.exit(1)
