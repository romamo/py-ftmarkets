"""The ``ftmarkets`` treaty app (Python 3.14+, ``cli`` extra)

Import this module only through ``ftmarkets.cli.main``, which checks the Python version
and that treaty is installed first. ``create_app`` takes a factory that builds the data
source for each run from treaty's network settings, so ``--proxy``, ``--no-proxy``, and the
CA bundle reach FT's ``requests.Session``, and tests run every command in-process through
``App.call`` against a fake source.
"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from importlib.metadata import version

import requests
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from pydantic_market_data.cli_models import HistoryQueryArgs, SecurityQueryArgs
from pydantic_market_data.interfaces import DataSource
from pydantic_market_data.models import (
    FlexibleDate,
    History,
    Price,
    PriceOnDate,
    PriceVerificationError,
    Security,
    SecurityQuery,
)
from treaty import App, CliExit, Ctx, Exit, NetworkSettings, Page, ParseError, RequiresAny

from .api import FTDataSource
from .client import FTClient
from .extract.scraper import Scraper, ScraperError

# FlexibleDate validates the format; the field is re-declared only so --help names it
_DATE_FORMATS = "YYYY-MM-DD, YYYY/MM/DD, or YYYYMMDD"
_IDENTIFIERS = ("isin", "symbol", "desc")


def _check_price_has_date(price: float | None, info: ValidationInfo) -> float | None:
    # A date that failed its own check is missing from info.data and reported there
    if price is not None and "date" in info.data and info.data["date"] is None:
        raise ValueError("--price requires --date: the price is checked on that date")
    return price


class LookupArgs(SecurityQueryArgs):
    """Lookup a security by ISIN, symbol, or description"""

    date: FlexibleDate | None = Field(
        None, description=f"Date the --price is checked on ({_DATE_FORMATS})"
    )
    security_type: str | None = Field(
        None,
        description="FT security type (ETF, Fund, Equity, Index), case-insensitive",
    )

    _price = field_validator("price")(_check_price_has_date)


class HistoryArgs(HistoryQueryArgs):
    """Fetch history for a security and optionally validate a price"""

    date: FlexibleDate | None = Field(
        None, description=f"Date the --price is checked on ({_DATE_FORMATS})"
    )

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
    if args.asset_class is not None and security.asset_class != args.asset_class:
        return False
    if args.security_type is not None and (
        not security.security_type
        or security.security_type.casefold() != args.security_type.casefold()
    ):
        return False
    if args.exchange and (
        not security.exchange or args.exchange.lower() not in security.exchange.lower()
    ):
        return False
    return True


def _price_matches(
    source: DataSource, security: Security, on: date, price: Price, ctx: Ctx
) -> bool:
    """Whether ``security`` traded near ``price`` on ``on``

    Only a failed price check means "no match"; scraper and HTTP errors propagate, so
    treaty reports them instead of the scan reading them as a mismatch.
    """
    try:
        return source.validate(security.symbol, on, price)
    except PriceVerificationError as exc:
        ctx.log("price check failed", symbol=str(security.symbol), reason=str(exc))
    return False


def _check_scan_cursor(cursor: str) -> None:
    """``lookup``'s own cursor: how many candidates the price check already went through"""
    if not (cursor.isascii() and cursor.isdigit()):
        raise ParseError(f"lookup cursor {cursor!r} is not a candidate count")


def run_lookup(source: DataSource, args: LookupArgs, ctx: Ctx) -> Page[Security]:
    """The matches in FT's relevance order; treaty slices them to ``--limit``

    With ``--price`` each candidate costs an FT history fetch, so the scan stops once it
    has a page and hands back how many candidates it went through as its cursor; the
    next page resumes the scan there.
    """
    query = args.isin or args.symbol or args.desc
    if query is None:
        raise ValueError("lookup ran without an identifier; RequiresAny should refuse that")
    if ctx.page is None:
        raise ValueError("lookup ran without a page request; it is a list command")
    found = [s for s in source.search(query) if _matches(s, args)]
    resumed = ctx.page.cursor

    if args.price is None or args.date is None:
        if not found:
            raise Exit.NOT_FOUND("Security not found", context={"query": query})
        return Page(items=found, total=len(found))

    price = Price(root=args.price)
    scanned = 0 if resumed is None else int(resumed)
    validated: list[Security] = []
    for security in found[scanned:]:
        if ctx.page.limit is not None and len(validated) >= ctx.page.limit:
            break
        scanned += 1
        if _price_matches(source, security, args.date, price, ctx):
            validated.append(security)
    if resumed is None and not validated:
        raise Exit.NOT_FOUND("Security not found", context={"query": query})
    more = scanned < len(found)
    return Page(items=validated, next_cursor=str(scanned) if more else None)


def run_history(source: DataSource, args: HistoryArgs, ctx: Ctx) -> HistoryResult:
    price_on = None
    if args.price is not None and args.date is not None:
        price_on = PriceOnDate(price=Price(root=args.price), date=args.date)
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
                context={"symbol": str(other.symbol), "price": args.price, "date": str(args.date)},
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
                context={
                    "symbol": str(security.symbol),
                    "price": args.price,
                    "date": str(args.date),
                },
            ) from exc
        if not validated:
            raise Exit.PRICE_MISMATCH(
                f"{security.symbol} did not trade near {args.price} on {args.date}",
                context={
                    "symbol": str(security.symbol),
                    "price": args.price,
                    "date": str(args.date),
                },
            )
    return HistoryResult(security=security, history=history, validated=validated)


_ISSUES = "https://github.com/romamo/py-ftmarkets/issues"
_REPORT = f"report it at {_ISSUES} with the command and the symbol"
_RATE_LIMIT_WAIT_MS = 30_000
"""The wait a 429 without a readable ``Retry-After`` asks for; FTClient already retried"""


def _retry_after_ms(raw: str | None) -> int | None:
    """``Retry-After`` in milliseconds, from delay seconds or an HTTP date; None when absent
    or unreadable"""
    if raw is None:
        return None
    raw = raw.strip()
    if raw.isascii() and raw.isdigit():
        return int(raw) * 1000
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, int((when - datetime.now(timezone.utc)).total_seconds() * 1000))


def _status_exit(exc: requests.HTTPError, context: dict[str, object]) -> CliExit | None:
    """The exit for FT's error status; None when ``exc`` carries no response"""
    response = exc.response
    if response is None:
        return None
    status = response.status_code
    context = {**context, "status_code": status}
    wait = _retry_after_ms(response.headers.get("Retry-After"))
    if status == 429:
        return Exit.RATE_LIMITED(
            "FT is rate limiting requests (HTTP 429).",
            code="RATE_LIMITED",
            detail=str(exc),
            context=context,
            retry_after_ms=_RATE_LIMIT_WAIT_MS if wait is None else wait,
            suggestion="wait error.retry_after_ms, then retry the same command",
        )
    if status >= 500:
        return Exit.UNAVAILABLE(
            f"FT answered HTTP {status}.",
            code="UPSTREAM_UNAVAILABLE",
            detail=str(exc),
            context=context,
            retry_after_ms=wait,
            suggestion="FT is having trouble; retry the same command with exponential back-off",
        )
    # A 4xx other than 429 refuses the request ftmarkets built: FT changed the URL or the
    # chart API it scrapes, or blocks this network; retrying the same request cannot help
    return Exit.UPSTREAM_CHANGED(
        f"FT refused the request (HTTP {status}).",
        code="UPSTREAM_REJECTED",
        detail=str(exc),
        context=context,
        suggestion=f"FT may have changed its site or be blocking this network or proxy; "
        f"if markets.ft.com opens in a browser from here, {_REPORT}",
    )


@contextmanager
def ft_failures(context: Mapping[str, object]) -> Iterator[None]:
    """Answers FT's failures with declared exit codes instead of ``HANDLER_CRASHED``

    Only these types are mapped; anything else, a programming error included, propagates.
    Transport failures (no connection, a timeout, FT's 5xx) are ``UNAVAILABLE`` and a 429 is
    ``RATE_LIMITED``, both retryable; a ``ScraperError`` or a 4xx is ``UPSTREAM_CHANGED``, and
    a TLS failure ``PRECONDITION``, neither retryable.
    """
    known = {key: value for key, value in context.items() if value is not None}
    try:
        yield
    except ScraperError as exc:
        raise Exit.UPSTREAM_CHANGED(
            "FT answered in a shape ftmarkets cannot read.",
            code="FT_PAGE_CHANGED",
            detail=str(exc),
            context=known,
            suggestion=f"FT likely changed its page or chart data; {_REPORT}",
        ) from exc
    except requests.HTTPError as exc:
        mapped = _status_exit(exc, _with_url(known, exc))
        if mapped is None:
            raise
        raise mapped from exc
    except requests.exceptions.SSLError as exc:
        raise Exit.PRECONDITION(
            "The TLS connection to FT failed.",
            code="TLS_FAILED",
            detail=str(exc),
            context=_with_url(known, exc),
            fix_required="set REQUESTS_CA_BUNDLE or SSL_CERT_FILE to the CA bundle that signed "
            "the certificate markets.ft.com presents here, such as a proxy's",
        ) from exc
    except requests.Timeout as exc:
        raise Exit.UNAVAILABLE(
            "FT did not answer in time.",
            code="UPSTREAM_TIMEOUT",
            detail=str(exc),
            context=_with_url(known, exc),
            suggestion="retry the same command with exponential back-off",
        ) from exc
    except (
        requests.ConnectionError,
        requests.exceptions.ChunkedEncodingError,
        requests.exceptions.RetryError,
    ) as exc:
        raise Exit.UNAVAILABLE(
            "Could not reach FT.",
            code="CONNECTION_FAILED",
            detail=str(exc),
            context=_with_url(known, exc),
            suggestion="check the network or --proxy, then retry with exponential back-off",
        ) from exc


def _with_url(context: dict[str, object], exc: requests.RequestException) -> dict[str, object]:
    request = exc.request
    if request is None or request.url is None:
        return context
    return {**context, "url": request.url}


def _model(cls: type) -> type[BaseModel]:
    """``cls`` as a pydantic model class; the adapters are registered for BaseModel only"""
    if not issubclass(cls, BaseModel):
        raise TypeError(f"{cls.__qualname__} is not a pydantic model")
    return cls


_FT_EXIT_CODES = ("RATE_LIMITED", "UNAVAILABLE", "UPSTREAM_CHANGED")
"""What ``ft_failures`` raises besides the implicit ``PRECONDITION``"""


def _identifiers(args: LookupArgs | HistoryArgs) -> dict[str, object]:
    return {"isin": args.isin, "symbol": args.symbol, "desc": args.desc}


SourceFactory = Callable[[NetworkSettings], DataSource]
"""Builds the data source a run uses, from the run's ``ctx.network``"""


def ft_client(network: NetworkSettings) -> FTClient:
    """An ``FTClient`` going out the way treaty resolved for this run: ``--proxy``, else the
    proxy variables with ``NO_PROXY`` applied to markets.ft.com, none under ``--no-proxy``;
    TLS verified against ``REQUESTS_CA_BUNDLE``/``SSL_CERT_FILE``, else the system store"""
    proxy = network.proxy_for(FTClient.BASE_URL)
    proxies = {} if proxy is None else {"http": proxy, "https": proxy}
    bundle = network.ca_bundle
    return FTClient(proxies=proxies, verify=True if bundle is None else str(bundle))


def ft_source(network: NetworkSettings) -> FTDataSource:
    """The live FT data source for one run"""
    return FTDataSource(Scraper(http_client=ft_client(network)))


def create_app(source_factory: SourceFactory) -> App:
    """The ``ftmarkets`` app; each run builds its source with ``source_factory(ctx.network)``"""
    app = App("ftmarkets", version=version("py-ftmarkets"))
    app.exit_code(
        "PRICE_MISMATCH",
        79,
        description="The security did not trade near --price on --date",
        retryable=False,
        side_effects="none",
        suggestion="check --price and --date, or drop them to fetch the history alone",
    )
    app.exit_code(
        "UPSTREAM_CHANGED",
        80,
        description="FT answered in a shape ftmarkets cannot read or refused its request (4xx)",
        retryable=False,
        side_effects="none",
        suggestion=f"FT likely changed its site; {_REPORT}",
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
        exit_codes=["NOT_FOUND", *_FT_EXIT_CODES],
        examples=[
            ("Look up an ISIN", "ftmarkets lookup --isin US0378331005"),
            (
                "Every match, as JSON",
                "ftmarkets lookup --isin DE000A0S9GB0 --limit 0 --format json",
            ),
            (
                "Only ETFs, in euros",
                "ftmarkets lookup --isin DE000A0S9GB0 --security-type ETF --currency EUR",
            ),
            (
                "Keep matches that traded near a price on a date",
                "ftmarkets lookup --isin DE000A0S9GB0 --price 117.81 --date 2025-12-12",
            ),
        ],
        has_network_io=True,
        external=True,
        ordered=True,
        default_limit=1,
        cursor_check=_check_scan_cursor,
        requires=[RequiresAny(_IDENTIFIERS)],
    )
    def lookup(args: LookupArgs, ctx: Ctx) -> Page[Security]:
        with ft_failures(_identifiers(args)):
            return run_lookup(source_factory(ctx.network), args, ctx)

    @app.command(
        "history",
        description="Fetch price history for a security and optionally validate a price",
        danger_level="safe",
        exit_codes=["NOT_FOUND", "PRICE_MISMATCH", *_FT_EXIT_CODES],
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
        with ft_failures(_identifiers(args)):
            return run_history(source_factory(ctx.network), args, ctx)

    return app


app = create_app(ft_source)
