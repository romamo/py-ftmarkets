"""The treaty CLI, run in-process through App.call against a fake data source"""

from datetime import date, datetime

import pytest
import requests
from pydantic_market_data.models import (
    OHLCV,
    History,
    HistoryPeriod,
    Price,
    PriceVerificationError,
    Security,
    SecurityQuery,
    Symbol,
)

pytest.importorskip("treaty")

from treaty import App, Envelope  # noqa: E402

from ftmarkets.api import FTDataSource  # noqa: E402
from ftmarkets.cli_app import create_app  # noqa: E402
from ftmarkets.extract.scraper import Scraper, ScraperError  # noqa: E402

APPLE = [
    Security(symbol="AAPL:NSQ", name="Apple Inc.", currency="USD", country="US"),
    Security(symbol="0R2V:LSE", name="Apple Inc.", currency="GBP", country="GB"),
    Security(symbol="APC:FRA", name="Apple Inc.", currency="EUR", country="DE"),
]


class FakeSource:
    """A DataSource with fixed answers: ``matching`` symbols pass validate, and
    ``priced`` (default ``matching``) are those resolve accepts for a price"""

    def __init__(
        self,
        securities: list[Security],
        matching: set[str] | None = None,
        priced: set[str] | None = None,
        failing: dict[str, Exception] | None = None,
    ):
        self.securities = securities
        self.matching = matching if matching is not None else set()
        self.priced = priced if priced is not None else self.matching
        self.failing = failing if failing is not None else {}
        self.validated: list[str] = []
        self.periods: list[HistoryPeriod] = []

    def search(self, query: str) -> list[Security]:
        return list(self.securities)

    def resolve(self, criteria: SecurityQuery) -> Security | None:
        for security in self.securities:
            if not criteria.price_on or str(security.symbol) in self.priced:
                return security
        return None

    def history(self, symbol: Symbol.Input, period: HistoryPeriod = HistoryPeriod.MO1) -> History:
        self.periods.append(period)
        security = next(s for s in self.securities if str(s.symbol) == str(symbol))
        candles = [
            OHLCV(date=datetime(2025, 1, 14), open=1, high=2, low=1, close=2),
            OHLCV(date=datetime(2025, 1, 15), open=2, high=3, low=2, close=3),
        ]
        return History(security=security, candles=candles)

    def get_price(self, symbol: Symbol.Input, date: date | None = None) -> Price | None:
        return None

    def validate(
        self,
        symbol: Symbol.Input,
        target_date: date,
        target_price: Price.Input,
        price_tolerance: float = 0.10,
    ) -> bool:
        self.validated.append(str(symbol))
        if str(symbol) in self.failing:
            raise self.failing[str(symbol)]
        if str(symbol) in self.matching:
            return True
        raise PriceVerificationError(
            "Price is outside daily range",
            symbol=str(symbol),
            actual_date=target_date,
            expected_price=150.0,
            actual_low=1.0,
            actual_high=2.0,
            actual_close=2.0,
            source="fake",
        )


def app_over(source: FakeSource) -> App:
    """The app with every run using ``source``, whatever the network settings"""
    return create_app(lambda network: source)


def error(envelope: Envelope) -> dict:
    found = envelope.to_json()["error"]
    assert isinstance(found, dict)
    return found


def pagination(envelope: Envelope) -> dict:
    found = envelope.to_json()["meta"]["pagination"]
    assert isinstance(found, dict)
    return found


def symbols(data: object) -> list[str]:
    assert isinstance(data, list)
    return [item["symbol"] for item in data]


@pytest.mark.parametrize("command", ["lookup", "history"])
def test_no_identifier_exits_2_naming_the_rule(command):
    envelope = app_over(FakeSource(APPLE)).call(command, {})
    assert envelope.exit_code == 2
    assert error(envelope)["context"]["rule"] == {"any_of": ["isin", "symbol", "desc"]}


@pytest.mark.parametrize("command", ["lookup", "history"])
def test_price_without_date_exits_2(command):
    source = FakeSource(APPLE)
    envelope = app_over(source).call(command, {"symbol": "AAPL", "price": 150.0})
    assert envelope.exit_code == 2
    assert error(envelope)["phase"] == "validation"
    assert error(envelope)["errors"][0]["field"] == "price"
    assert source.validated == []


@pytest.mark.parametrize("command", ["lookup", "history"])
@pytest.mark.parametrize("value", ["15.01.2025", "15/01/2025", "01/02/2025", "Jan 15 2025"])
def test_non_year_first_date_exits_2(command, value):
    source = FakeSource(APPLE, matching={"AAPL:NSQ"})
    envelope = app_over(source).call(command, {"symbol": "AAPL", "price": 150.0, "date": value})
    assert envelope.exit_code == 2
    assert error(envelope)["phase"] == "validation"
    assert error(envelope)["errors"][0]["field"] == "date"
    # pydantic-market-data's FlexibleDate names the accepted formats
    assert "YYYY-MM-DD, YYYY/MM/DD or YYYYMMDD" in error(envelope)["errors"][0]["message"]
    assert source.validated == []


@pytest.mark.parametrize("command", ["lookup", "history"])
@pytest.mark.parametrize("value", ["2025-13-01", "2024-02-30"])
def test_impossible_date_exits_2(command, value):
    envelope = app_over(FakeSource(APPLE)).call(
        command, {"symbol": "AAPL", "price": 150.0, "date": value}
    )
    assert envelope.exit_code == 2
    assert error(envelope)["phase"] == "validation"
    assert error(envelope)["errors"][0]["field"] == "date"


@pytest.mark.parametrize("command", ["lookup", "history"])
@pytest.mark.parametrize("value", ["2025-01-15", "2025/01/15", "20250115"])
def test_year_first_date_formats_are_accepted(command, value):
    source = FakeSource(APPLE, matching={"AAPL:NSQ"})
    envelope = app_over(source).call(command, {"symbol": "AAPL", "price": 150.0, "date": value})
    assert envelope.exit_code == 0
    assert source.validated == ["AAPL:NSQ"]


def test_bad_period_exits_2():
    source = FakeSource(APPLE)
    envelope = app_over(source).call("history", {"symbol": "AAPL", "period": "7y"})
    assert envelope.exit_code == 2
    assert error(envelope)["errors"][0]["field"] == "period"
    assert source.periods == []


def test_lookup_keeps_the_source_order():
    envelope = app_over(FakeSource(APPLE)).call("lookup", {"isin": "US0378331005", "limit": 0})
    assert envelope.exit_code == 0
    assert symbols(envelope.data) == ["AAPL:NSQ", "0R2V:LSE", "APC:FRA"]


def test_lookup_defaults_to_one_result():
    envelope = app_over(FakeSource(APPLE)).call("lookup", {"isin": "US0378331005"})
    assert symbols(envelope.data) == ["AAPL:NSQ"]
    assert pagination(envelope)["has_more"] is True
    assert pagination(envelope)["total"] == 3


def test_lookup_pages_keep_the_source_order():
    app = app_over(FakeSource(APPLE))
    first = app.call("lookup", {"isin": "US0378331005", "limit": 2})
    assert symbols(first.data) == ["AAPL:NSQ", "0R2V:LSE"]
    cursor = pagination(first)["next_cursor"]
    second = app.call("lookup", {"isin": "US0378331005", "limit": 2, "cursor": cursor})
    assert symbols(second.data) == ["APC:FRA"]
    assert pagination(second)["has_more"] is False


def test_lookup_cursor_from_other_arguments_exits_2():
    app = app_over(FakeSource(APPLE))
    cursor = pagination(app.call("lookup", {"isin": "US0378331005"}))["next_cursor"]
    envelope = app.call("lookup", {"isin": "US0378331005", "currency": "EUR", "cursor": cursor})
    assert envelope.exit_code == 2
    assert error(envelope)["code"] == "INVALID_CURSOR"


def test_lookup_filters_by_currency():
    envelope = app_over(FakeSource(APPLE)).call(
        "lookup", {"isin": "US0378331005", "currency": "EUR", "limit": 0}
    )
    assert symbols(envelope.data) == ["APC:FRA"]


VODAFONE = [
    Security(symbol="VOD:LSE", name="Vodafone", currency="GBX", country="GB"),
    Security(symbol="VUSA:LSE:GBP", name="Vanguard", currency="GBP", country="GB"),
    Security(symbol="VOD:NSQ", name="Vodafone", currency="USD", country="US"),
]


def test_lookup_currency_gbx_keeps_pence_lines():
    # GBX is in the --currency schema enum on pydantic-market-data 0.9 (#8)
    envelope = app_over(FakeSource(VODAFONE)).call(
        "lookup", {"desc": "Vodafone", "currency": "GBX", "limit": 0}
    )
    assert envelope.exit_code == 0
    assert symbols(envelope.data) == ["VOD:LSE"]


def test_lookup_currency_gbp_skips_pence_lines():
    envelope = app_over(FakeSource(VODAFONE)).call(
        "lookup", {"desc": "Vodafone", "currency": "GBP", "limit": 0}
    )
    assert symbols(envelope.data) == ["VUSA:LSE:GBP"]


ETF = Security(symbol="EXS1:GER", name="iShares", asset_class="equity", security_type="ETF")
INDEX = Security(symbol="DAX:GER", name="DAX", asset_class="index", security_type="Index")


def test_lookup_filters_by_asset_class():
    source = FakeSource([*APPLE, ETF, INDEX])
    by_class = app_over(source).call("lookup", {"desc": "x", "asset_class": "index", "limit": 0})
    assert symbols(by_class.data) == ["DAX:GER"]
    equity = app_over(source).call("lookup", {"desc": "x", "asset_class": "equity", "limit": 0})
    assert symbols(equity.data) == ["EXS1:GER"]


def test_lookup_asset_class_outside_the_enum_exits_2():
    envelope = app_over(FakeSource([ETF])).call("lookup", {"desc": "x", "asset_class": "ETF"})
    assert envelope.exit_code == 2
    assert error(envelope)["errors"][0]["field"] == "asset-class"


def test_lookup_filters_by_security_type_in_any_case():
    source = FakeSource([*APPLE, ETF, INDEX])
    envelope = app_over(source).call("lookup", {"desc": "x", "security_type": "etf", "limit": 0})
    assert symbols(envelope.data) == ["EXS1:GER"]


def test_lookup_not_found():
    envelope = app_over(FakeSource([])).call("lookup", {"symbol": "NOPE"})
    assert envelope.exit_code == 5
    assert error(envelope)["code"] == "NOT_FOUND"


def test_lookup_price_validation_keeps_only_matches():
    source = FakeSource(APPLE, matching={"APC:FRA"})
    envelope = app_over(source).call(
        "lookup", {"isin": "US0378331005", "price": 150.0, "date": "2025-01-15", "limit": 0}
    )
    assert envelope.exit_code == 0
    assert symbols(envelope.data) == ["APC:FRA"]
    assert source.validated == ["AAPL:NSQ", "0R2V:LSE", "APC:FRA"]
    assert pagination(envelope)["has_more"] is False


def test_lookup_price_validation_stops_at_the_limit():
    source = FakeSource(APPLE, matching={"AAPL:NSQ", "APC:FRA"})
    envelope = app_over(source).call(
        "lookup", {"isin": "US0378331005", "price": 150.0, "date": "2025-01-15"}
    )
    assert symbols(envelope.data) == ["AAPL:NSQ"]
    assert source.validated == ["AAPL:NSQ"]
    assert pagination(envelope)["has_more"] is True


def test_lookup_price_validation_next_page_resumes_the_scan():
    source = FakeSource(APPLE, matching={"AAPL:NSQ", "APC:FRA"})
    app = app_over(source)
    query = {"isin": "US0378331005", "price": 150.0, "date": "2025-01-15"}
    cursor = pagination(app.call("lookup", query))["next_cursor"]
    second = app.call("lookup", {**query, "cursor": cursor})
    assert symbols(second.data) == ["APC:FRA"]
    assert pagination(second)["has_more"] is False
    # The second page validates only the candidates after the first page's scan
    assert source.validated == ["AAPL:NSQ", "0R2V:LSE", "APC:FRA"]


def test_lookup_price_validation_last_page_may_be_empty():
    source = FakeSource(APPLE, matching={"AAPL:NSQ"})
    app = app_over(source)
    query = {"isin": "US0378331005", "price": 150.0, "date": "2025-01-15"}
    cursor = pagination(app.call("lookup", query))["next_cursor"]
    second = app.call("lookup", {**query, "cursor": cursor})
    assert second.exit_code == 0
    assert second.data == []
    assert pagination(second)["has_more"] is False


def test_lookup_price_validation_without_a_match_is_not_found():
    envelope = app_over(FakeSource(APPLE)).call(
        "lookup", {"isin": "US0378331005", "price": 150.0, "date": "20250115"}
    )
    assert envelope.exit_code == 5


def server_error() -> requests.HTTPError:
    response = requests.Response()
    response.status_code = 503
    return requests.HTTPError("503 Server Error", response=response)


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (ScraperError("Malformed chart response for 0R2V:LSE"), "FT_PAGE_CHANGED"),
        (server_error(), "UPSTREAM_UNAVAILABLE"),
    ],
)
def test_lookup_price_check_errors_propagate(failure, code):
    # A scraper or HTTP failure is an error, not "this candidate did not match"
    source = FakeSource(APPLE, matching={"APC:FRA"}, failing={"0R2V:LSE": failure})
    envelope = app_over(source).call(
        "lookup", {"isin": "US0378331005", "price": 150.0, "date": "2025-01-15", "limit": 0}
    )
    assert envelope.exit_code not in (0, 5)
    assert error(envelope)["code"] == code
    assert source.validated == ["AAPL:NSQ", "0R2V:LSE"]


class FailingHistoryScraper(Scraper):
    """Finds APPLE, but every history fetch fails with ``failure``"""

    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    def search(self, query: Symbol.Input) -> list[Security]:
        return list(APPLE)

    def get_history(self, symbol: Symbol.Input, days: int = 30) -> History:
        raise self.failure


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (ScraperError("Malformed chart response for AAPL:NSQ"), "FT_PAGE_CHANGED"),
        (server_error(), "UPSTREAM_UNAVAILABLE"),
    ],
)
def test_history_price_check_errors_propagate_from_resolve(failure, code):
    # FTDataSource.resolve() lets the error through, so history --price reports it
    source = FTDataSource(scraper_instance=FailingHistoryScraper(failure))
    envelope = app_over(source).call(
        "history", {"symbol": "AAPL", "price": 150.0, "date": "2025-01-15"}
    )
    assert envelope.exit_code not in (0, 5, 79)
    assert error(envelope)["code"] == code


def test_history_returns_security_and_ordered_candles():
    source = FakeSource(APPLE)
    envelope = app_over(source).call("history", {"symbol": "AAPL", "period": "1y"})
    assert envelope.exit_code == 0
    assert source.periods == [HistoryPeriod.Y1]
    assert envelope.data["security"]["symbol"] == "AAPL:NSQ"
    assert envelope.data["validated"] is None
    dates = [c["date"] for c in envelope.data["history"]["candles"]]
    assert dates == ["2025-01-14T00:00:00", "2025-01-15T00:00:00"]


def test_history_not_found():
    envelope = app_over(FakeSource([])).call("history", {"symbol": "NOPE"})
    assert envelope.exit_code == 5
    assert error(envelope)["code"] == "NOT_FOUND"


def test_history_price_validation_passes():
    source = FakeSource(APPLE, matching={"AAPL:NSQ"})
    envelope = app_over(source).call(
        "history", {"symbol": "AAPL", "price": 150.0, "date": "2025-01-15"}
    )
    assert envelope.exit_code == 0
    assert envelope.data["validated"] is True
    assert source.validated == ["AAPL:NSQ"]


def test_history_price_mismatch_on_resolved_security():
    # resolve() matches on the price but validate() disagrees
    source = FakeSource(APPLE, priced={"AAPL:NSQ"})
    envelope = app_over(source).call(
        "history", {"symbol": "AAPL", "price": 150.0, "date": "2025-01-15"}
    )
    assert envelope.exit_code == 79
    assert error(envelope)["code"] == "PRICE_MISMATCH"
    assert error(envelope)["retryable"] is False


def test_history_price_mismatch_when_no_candidate_trades_near_the_price():
    envelope = app_over(FakeSource(APPLE)).call(
        "history", {"symbol": "AAPL", "price": 150.0, "date": "2025-01-15"}
    )
    assert envelope.exit_code == 79
    assert error(envelope)["context"]["symbol"] == "AAPL:NSQ"
