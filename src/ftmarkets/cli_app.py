"""The ``ftmarkets`` treaty app (Python 3.14+, ``cli`` extra)

Import this module only through ``ftmarkets.cli.main``, which checks the Python version
and that treaty is installed first. ``create_app`` takes the data source, so tests run
every command in-process through ``App.call`` against a fake source.
"""

from datetime import date
from importlib.metadata import version

import requests
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from pydantic_market_data.cli_models import HistoryQueryArgs, SecurityQueryArgs
from pydantic_market_data.interfaces import DataSource
from pydantic_market_data.models import (
    History,
    Price,
    PriceOnDate,
    PriceVerificationError,
    Security,
    SecurityQuery,
)
from treaty import App, Ctx, Exit, RequiresAny

from .api import FTDataSource
from .extract.scraper import ScraperError
from .utils import parse_date

_DATE_FORMATS = "YYYY-MM-DD, YYYYMMDD, or DD/MM/YYYY"
_IDENTIFIERS = ("isin", "symbol", "desc")


def _parsed_date(value: str) -> date:
    parsed = parse_date(value)
    if parsed is None:
        raise ValueError(f"date {value!r} is not {_DATE_FORMATS}")
    return parsed.date()


def _check_date(value: str | None) -> str | None:
    if value is not None:
        _parsed_date(value)
    return value


def _check_price_has_date(price: float | None, info: ValidationInfo) -> float | None:
    # A date that failed its own check is missing from info.data and reported there
    if price is not None and "date" in info.data and info.data["date"] is None:
        raise ValueError("--price requires --date: the price is checked on that date")
    return price


class LookupArgs(SecurityQueryArgs):
    """Lookup a security by ISIN, symbol, or description"""

    _date = field_validator("date")(_check_date)
    _price = field_validator("price")(_check_price_has_date)


class HistoryArgs(HistoryQueryArgs):
    """Fetch history for a security and optionally validate a price"""

    _date = field_validator("date")(_check_date)
    _price = field_validator("price")(_check_price_has_date)


class HistoryResult(BaseModel):
    """A resolved security, its history, and the outcome of the price check"""

    security: Security = Field(description="The security the identifiers resolved to")
    history: History = Field(description="Price history of the resolved security")
    validated: bool | None = Field(
        description="True when --price matched on --date; null when no --price was given"
    )


def _matches(security: Security, args: LookupArgs) -> bool:
    if args.currency and (
        not security.currency or str(security.currency).upper() != str(args.currency).upper()
    ):
        return False
    if args.country and (
        not security.country or str(security.country).upper() != str(args.country).upper()
    ):
        return False
    if args.asset_class:
        wanted = str(args.asset_class).upper()
        kinds = {
            security.asset_class.value.upper() if security.asset_class else None,
            security.security_type.upper() if security.security_type else None,
        }
        if wanted not in kinds:
            return False
    if args.exchange and (
        not security.exchange or args.exchange.lower() not in security.exchange.lower()
    ):
        return False
    return True


def _price_matches(
    source: DataSource, security: Security, on: date, price: Price, ctx: Ctx
) -> bool:
    symbol = str(security.symbol)
    try:
        return source.validate(security.symbol, on, price)
    except PriceVerificationError as exc:
        ctx.log("price check failed", symbol=symbol, reason=str(exc))
    except ScraperError as exc:
        ctx.debug("scraper error during price check", symbol=symbol, reason=str(exc))
    except requests.exceptions.HTTPError as exc:
        ctx.debug("HTTP error during price check", symbol=symbol, reason=str(exc))
    return False


def run_lookup(source: DataSource, args: LookupArgs, ctx: Ctx) -> list[Security]:
    query = args.isin or args.symbol or args.desc
    if query is None:
        raise ValueError("lookup ran without an identifier; RequiresAny should refuse that")
    found = [s for s in source.search(query) if _matches(s, args)]

    if args.price is not None and args.date is not None:
        on = _parsed_date(args.date)
        price = Price(root=args.price)
        validated: list[Security] = []
        for security in found:
            if args.limit > 0 and len(validated) >= args.limit:
                break
            if _price_matches(source, security, on, price, ctx):
                validated.append(security)
        found = validated
    elif args.limit > 0:
        found = found[: args.limit]

    if not found:
        raise Exit.NOT_FOUND("Security not found", context={"query": query})
    return found


def run_history(source: DataSource, args: HistoryArgs, ctx: Ctx) -> HistoryResult:
    price_on = None
    if args.price is not None and args.date is not None:
        price_on = PriceOnDate(price=Price(root=args.price), date=_parsed_date(args.date))
    criteria = SecurityQuery(
        isin=args.isin,
        symbol=args.symbol,
        description=args.desc,
        exchange=args.exchange,
        price_on=[price_on] if price_on is not None else None,
    )
    identifiers = {"isin": args.isin, "symbol": args.symbol, "desc": args.desc}

    security = source.resolve(criteria)
    if security is None:
        unpriced = criteria.model_copy(update={"price_on": None})
        if price_on is not None and (other := source.resolve(unpriced)) is not None:
            raise Exit.PRICE_MISMATCH(
                f"{other.symbol} did not trade near {args.price} on {args.date}",
                context={"symbol": str(other.symbol), "price": args.price, "date": args.date},
            )
        raise Exit.NOT_FOUND("Could not resolve the security", context=identifiers)
    ctx.log("resolved", symbol=str(security.symbol))

    history = source.history(security.symbol, period=args.period)
    if not history.candles:
        raise Exit.NOT_FOUND(
            "No history found", context={"symbol": str(security.symbol), "period": args.period}
        )

    validated = None
    if price_on is not None:
        try:
            validated = source.validate(security.symbol, price_on.date, price_on.price)
        except PriceVerificationError as exc:
            raise Exit.PRICE_MISMATCH(
                str(exc),
                context={"symbol": str(security.symbol), "price": args.price, "date": args.date},
            ) from exc
        if not validated:
            raise Exit.PRICE_MISMATCH(
                f"{security.symbol} did not trade near {args.price} on {args.date}",
                context={"symbol": str(security.symbol), "price": args.price, "date": args.date},
            )
    return HistoryResult(security=security, history=history, validated=validated)


def _model(cls: type) -> type[BaseModel]:
    """``cls`` as a pydantic model class; the adapters are registered for BaseModel only"""
    if not issubclass(cls, BaseModel):
        raise TypeError(f"{cls.__qualname__} is not a pydantic model")
    return cls


def create_app(source: DataSource) -> App:
    """The ``ftmarkets`` app over ``source``"""
    app = App("ftmarkets", version=version("py-ftmarkets"))
    app.exit_code(
        "PRICE_MISMATCH",
        79,
        description="The security did not trade near --price on --date",
        retryable=False,
        side_effects="none",
        suggestion="check --price and --date, or drop them to fetch the history alone",
    )
    app.args_adapter(
        BaseModel,
        schema=lambda cls: _model(cls).model_json_schema(by_alias=False),
        validate=lambda cls, data: _model(cls).model_validate(data, by_name=True, by_alias=False),
    )
    app.output_adapter(
        BaseModel,
        schema=lambda cls: _model(cls).model_json_schema(mode="serialization"),
        dump=lambda obj: obj.model_dump(mode="json", by_alias=True),
        none_as_empty=True,
    )

    @app.command(
        "lookup",
        description="Lookup a security by ISIN, symbol, or description, in FT's relevance order",
        danger_level="safe",
        exit_codes=["NOT_FOUND"],
        examples=[
            ("Look up an ISIN", "ftmarkets lookup --isin US0378331005"),
            (
                "Every match, as JSON",
                "ftmarkets lookup --isin DE000A0S9GB0 --limit 0 --format json",
            ),
            (
                "Keep matches that traded near a price on a date",
                "ftmarkets lookup --isin DE000A0S9GB0 --price 117.81 --date 2025-12-12",
            ),
        ],
        has_network_io=True,
        external=True,
        paginated=False,
        ordered=True,
        requires=[RequiresAny(_IDENTIFIERS)],
    )
    def lookup(args: LookupArgs, ctx: Ctx) -> list[Security]:
        return run_lookup(source, args, ctx)

    @app.command(
        "history",
        description="Fetch price history for a security and optionally validate a price",
        danger_level="safe",
        exit_codes=["NOT_FOUND", "PRICE_MISMATCH"],
        examples=[
            ("One month of history", "ftmarkets history --isin DE000A0S9GB0"),
            ("One year of history", "ftmarkets history --symbol AAPL:NSQ --period 1y"),
            (
                "Validate a trade price",
                "ftmarkets history --isin DE000A0S9GB0 --price 120.50 --date 2025-01-15",
            ),
        ],
        has_network_io=True,
        external=True,
        requires=[RequiresAny(_IDENTIFIERS)],
    )
    def history(args: HistoryArgs, ctx: Ctx) -> HistoryResult:
        return run_history(source, args, ctx)

    return app


app = create_app(FTDataSource())
