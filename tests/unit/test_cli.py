"""The treaty CLI, run in-process through App.call against a fake data source"""

from datetime import date, datetime

import pytest
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

from treaty import Envelope  # noqa: E402

from ftmarkets.cli_app import create_app  # noqa: E402

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
    ):
        self.securities = securities
        self.matching = matching if matching is not None else set()
        self.priced = priced if priced is not None else self.matching
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


def error(envelope: Envelope) -> dict:
    found = envelope.to_json()["error"]
    assert isinstance(found, dict)
    return found


def symbols(data: object) -> list[str]:
    assert isinstance(data, list)
    return [item["symbol"] for item in data]


@pytest.mark.parametrize("command", ["lookup", "history"])
def test_no_identifier_exits_2_naming_the_rule(command):
    envelope = create_app(FakeSource(APPLE)).call(command, {})
    assert envelope.exit_code == 2
    assert error(envelope)["context"]["rule"] == {"any_of": ["isin", "symbol", "desc"]}


@pytest.mark.parametrize("command", ["lookup", "history"])
def test_price_without_date_exits_2(command):
    source = FakeSource(APPLE)
    envelope = create_app(source).call(command, {"symbol": "AAPL", "price": 150.0})
    assert envelope.exit_code == 2
    assert error(envelope)["phase"] == "validation"
    assert error(envelope)["errors"][0]["field"] == "price"
    assert source.validated == []


def test_malformed_date_exits_2():
    envelope = create_app(FakeSource(APPLE)).call(
        "lookup", {"symbol": "AAPL", "price": 150.0, "date": "15.01.2025"}
    )
    assert envelope.exit_code == 2
    assert error(envelope)["errors"][0]["field"] == "date"


def test_bad_period_exits_2():
    source = FakeSource(APPLE)
    envelope = create_app(source).call("history", {"symbol": "AAPL", "period": "7y"})
    assert envelope.exit_code == 2
    assert error(envelope)["errors"][0]["field"] == "period"
    assert source.periods == []


def test_lookup_keeps_the_source_order():
    envelope = create_app(FakeSource(APPLE)).call("lookup", {"isin": "US0378331005", "limit": 0})
    assert envelope.exit_code == 0
    assert symbols(envelope.data) == ["AAPL:NSQ", "0R2V:LSE", "APC:FRA"]


def test_lookup_defaults_to_one_result():
    envelope = create_app(FakeSource(APPLE)).call("lookup", {"isin": "US0378331005"})
    assert symbols(envelope.data) == ["AAPL:NSQ"]


def test_lookup_filters_by_currency():
    envelope = create_app(FakeSource(APPLE)).call(
        "lookup", {"isin": "US0378331005", "currency": "EUR", "limit": 0}
    )
    assert symbols(envelope.data) == ["APC:FRA"]


def test_lookup_filters_by_asset_class_or_security_type():
    etf = Security(symbol="EXS1:GER", name="iShares", asset_class="equity", security_type="ETF")
    source = FakeSource([*APPLE, etf])
    by_type = create_app(source).call("lookup", {"desc": "x", "asset_class": "etf", "limit": 0})
    by_class = create_app(source).call("lookup", {"desc": "x", "asset_class": "EQUITY", "limit": 0})
    assert symbols(by_type.data) == ["EXS1:GER"]
    assert symbols(by_class.data) == ["EXS1:GER"]


def test_lookup_not_found():
    envelope = create_app(FakeSource([])).call("lookup", {"symbol": "NOPE"})
    assert envelope.exit_code == 5
    assert error(envelope)["code"] == "NOT_FOUND"


def test_lookup_price_validation_keeps_only_matches():
    source = FakeSource(APPLE, matching={"APC:FRA"})
    envelope = create_app(source).call(
        "lookup", {"isin": "US0378331005", "price": 150.0, "date": "2025-01-15", "limit": 0}
    )
    assert envelope.exit_code == 0
    assert symbols(envelope.data) == ["APC:FRA"]
    assert source.validated == ["AAPL:NSQ", "0R2V:LSE", "APC:FRA"]


def test_lookup_price_validation_stops_at_the_limit():
    source = FakeSource(APPLE, matching={"AAPL:NSQ", "APC:FRA"})
    envelope = create_app(source).call(
        "lookup", {"isin": "US0378331005", "price": 150.0, "date": "2025-01-15"}
    )
    assert symbols(envelope.data) == ["AAPL:NSQ"]
    assert source.validated == ["AAPL:NSQ"]


def test_lookup_price_validation_without_a_match_is_not_found():
    envelope = create_app(FakeSource(APPLE)).call(
        "lookup", {"isin": "US0378331005", "price": 150.0, "date": "20250115"}
    )
    assert envelope.exit_code == 5


def test_history_returns_security_and_ordered_candles():
    source = FakeSource(APPLE)
    envelope = create_app(source).call("history", {"symbol": "AAPL", "period": "1y"})
    assert envelope.exit_code == 0
    assert source.periods == [HistoryPeriod.Y1]
    assert envelope.data["security"]["symbol"] == "AAPL:NSQ"
    assert envelope.data["validated"] is None
    dates = [c["date"] for c in envelope.data["history"]["candles"]]
    assert dates == ["2025-01-14T00:00:00", "2025-01-15T00:00:00"]


def test_history_not_found():
    envelope = create_app(FakeSource([])).call("history", {"symbol": "NOPE"})
    assert envelope.exit_code == 5
    assert error(envelope)["code"] == "NOT_FOUND"


def test_history_price_validation_passes():
    source = FakeSource(APPLE, matching={"AAPL:NSQ"})
    envelope = create_app(source).call(
        "history", {"symbol": "AAPL", "price": 150.0, "date": "2025-01-15"}
    )
    assert envelope.exit_code == 0
    assert envelope.data["validated"] is True
    assert source.validated == ["AAPL:NSQ"]


def test_history_price_mismatch_on_resolved_security():
    # resolve() matches on the price but validate() disagrees
    source = FakeSource(APPLE, priced={"AAPL:NSQ"})
    envelope = create_app(source).call(
        "history", {"symbol": "AAPL", "price": 150.0, "date": "2025-01-15"}
    )
    assert envelope.exit_code == 79
    assert error(envelope)["code"] == "PRICE_MISMATCH"
    assert error(envelope)["retryable"] is False


def test_history_price_mismatch_when_no_candidate_trades_near_the_price():
    envelope = create_app(FakeSource(APPLE)).call(
        "history", {"symbol": "AAPL", "price": 150.0, "date": "2025-01-15"}
    )
    assert envelope.exit_code == 79
    assert error(envelope)["context"]["symbol"] == "AAPL:NSQ"
