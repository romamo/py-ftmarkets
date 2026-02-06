import json
import sys

from .. import api


def setup_parser(subparsers):
    parser = subparsers.add_parser("lookup", help="Lookup a ticker symbol")
    parser.add_argument("--isin", help="ISIN of the security")
    parser.add_argument("--symbol", help="Symbol")
    parser.add_argument("--desc", help="Description")
    parser.add_argument("--exchange", help="Preferred exchange")
    parser.add_argument("--currency", help="Filter by currency (e.g. EUR, USD)")
    parser.add_argument("--country", help="Filter by country (e.g. DE, US)")
    parser.add_argument("--asset-class", help="Filter by asset class (ETF, Equity, Fund, Index)")
    parser.add_argument(
        "--limit", type=int, default=100, help="Limit number of results (0 for all)"
    )
    parser.add_argument(
        "--format",
        choices=["text", "json", "xml"],
        default="text",
        help="Output format",
    )
    parser.add_argument("--price", type=float, help="Validation price")
    parser.add_argument("--date", help="Validation date")
    parser.set_defaults(func=handle)


def handle(args):
    preferred = [args.exchange] if args.exchange else None

    symbols = api.resolve_ticker(
        isin=args.isin,
        symbol=args.symbol,
        description=args.desc,
        preferred_exchanges=preferred,
        target_price=args.price,
        target_date=args.date,
        currency=args.currency,
        country=args.country,
        asset_class=args.asset_class,
        limit=args.limit,
    )

    if not symbols:
        print("Ticker not found", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        data = []
        for s in symbols:
            d = s.model_dump()
            data.append(d)
        print(json.dumps(data, indent=2, default=str))
    elif args.format == "xml":
        print("<Results>")
        for s in symbols:
            print("  <Symbol>")
            print(f"    <Ticker>{s.ticker}</Ticker>")
            print(f"    <Name>{s.name}</Name>")
            print(f"    <Exchange>{s.exchange or ''}</Exchange>")
            print(f"    <Country>{s.country or ''}</Country>")
            print(f"    <Currency>{s.currency or ''}</Currency>")
            print(f"    <AssetClass>{s.asset_class or ''}</AssetClass>")
            print("  </Symbol>")
        print("</Results>")
    else:
        for s in symbols:
            print(s.ticker)
