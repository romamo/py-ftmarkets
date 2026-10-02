"""FTDataSource behaviour from #9, against a fake scraper (no network, no patching)"""

from datetime import date, datetime

import pytest
import requests
from pydantic_market_data.models import (
    OHLCV,
    AssetClass,
    History,
    HistoryPeriod,
    PriceOnDate,
    Security,
    SecurityQuery,
    Symbol,
)

from ftmarkets.api import FTDataSource
from ftmarkets.extract.scraper import Scraper, ScraperError


class FakeScraper(Scraper):
    """Returns canned search results and history, and records each history request"""

    def __init__(
        self,
        securities: list[Security] | None = None,
        candles: list[OHLCV] | None = None,
        history_error: Exception | None = None,
    ) -> None:
        self.securities = securities or []
        self.candles = candles or []
        self.history_error = history_error
        self.history_days: list[int] = []

    def search(self, query: Symbol.Input) -> list[Security]:
        return list(self.securities)

    def get_history(self, symbol: Symbol.Input, days: int = 30) -> History:
        self.history_days.append(days)
        if self.history_error is not None:
            raise self.history_error
        symbol_val = Symbol(root=symbol) if isinstance(symbol, str) else symbol
        return History(
            security=Security(symbol=symbol_val, name=symbol_val.root), candles=self.candles
        )


def _source(fake: FakeScraper, today: date = date(2026, 10, 2)) -> FTDataSource:
    return FTDataSource(scraper_instance=fake, today=lambda: today)


_PRICED = SecurityQuery(symbol="X", price_on=PriceOnDate(price=100.0, date=date(2026, 9, 1)))


# 1. resolve() lets everything but PriceVerificationError propagate


@pytest.mark.parametrize(
    "error",
    [
        ScraperError("chart response malformed"),
        requests.exceptions.ConnectionError("down"),
        requests.exceptions.HTTPError("503"),
        TypeError("bug"),
    ],
)
def test_resolve_propagates_history_errors(error: Exception) -> None:
    fake = FakeScraper([Security(symbol="X:GER", name="X")], history_error=error)
    with pytest.raises(type(error)):
        _source(fake).resolve(_PRICED)


def test_resolve_price_mismatch_moves_to_next_candidate() -> None:
    first = Security(symbol="X:GER", name="X")
    second = Security(symbol="X:LSE", name="X")

    class PerSymbol(FakeScraper):
        def get_history(self, symbol: Symbol.Input, days: int = 30) -> History:
            close = 500.0 if str(symbol) == "X:GER" else 100.0
            self.candles = [OHLCV(date=datetime(2026, 9, 1), close=close)]
            return super().get_history(symbol, days)

    result = _source(PerSymbol([first, second])).resolve(_PRICED)
    assert result is not None
    assert str(result.symbol) == "X:LSE"


# 2. asset_class compares the enum directly, for every class


def test_resolve_filters_on_any_asset_class() -> None:
    equity = Security(symbol="E:GER", name="E", asset_class=AssetClass.EQUITY)
    bond = Security(symbol="B:GER", name="B", asset_class=AssetClass.FIXED_INCOME)
    fake = FakeScraper([equity, bond])

    found = _source(fake).resolve(SecurityQuery(symbol="X", asset_class="fixed_income"))
    assert found is not None
    assert str(found.symbol) == "B:GER"

    found = _source(fake).resolve(SecurityQuery(symbol="X", asset_class=AssetClass.EQUITY))
    assert found is not None
    assert str(found.symbol) == "E:GER"


def test_resolve_asset_class_without_match_is_none() -> None:
    fake = FakeScraper([Security(symbol="E:GER", name="E", asset_class=AssetClass.EQUITY)])
    assert _source(fake).resolve(SecurityQuery(symbol="X", asset_class="commodity")) is None


def test_resolve_asset_class_skips_unclassified() -> None:
    fake = FakeScraper([Security(symbol="F:GER", name="F")])
    assert _source(fake).resolve(SecurityQuery(symbol="X", asset_class="equity")) is None


# 3. resolve() honours criteria.exchange


def test_resolve_filters_on_exchange_substring_case_insensitive() -> None:
    ger = Security(symbol="4GLD:GER", name="Gold", exchange="Xetra")
    nyse = Security(symbol="4GLD:NYQ", name="Gold", exchange="New York Stock Exchange NYSE")
    fake = FakeScraper([ger, nyse, Security(symbol="4GLD", name="Gold")])

    found = _source(fake).resolve(SecurityQuery(symbol="4GLD", exchange="nyse"))
    assert found is not None
    assert str(found.symbol) == "4GLD:NYQ"
    assert _source(fake).resolve(SecurityQuery(symbol="4GLD", exchange="LSE")) is None


# 4. YTD counts from 1 January; every period maps strictly


@pytest.mark.parametrize(
    ("today", "days"),
    [(date(2026, 1, 1), 1), (date(2026, 3, 1), 60), (date(2024, 12, 31), 366)],
)
def test_history_ytd_counts_days_since_new_year(today: date, days: int) -> None:
    fake = FakeScraper()
    _source(fake, today=today).history("X:GER", HistoryPeriod.YTD)
    assert fake.history_days == [days]


@pytest.mark.parametrize("period", list(HistoryPeriod))
def test_history_maps_every_period(period: HistoryPeriod) -> None:
    fake = FakeScraper()
    _source(fake).history("X:GER", period)
    assert len(fake.history_days) == 1
    assert fake.history_days[0] > 0


# 5. D1 fetches several days and keeps the latest candle


def test_history_d1_returns_last_candle_of_a_wider_window() -> None:
    friday = OHLCV(date=datetime(2026, 9, 25), close=101.0)
    thursday = OHLCV(date=datetime(2026, 9, 24), close=100.0)
    fake = FakeScraper(candles=[thursday, friday])

    hist = _source(fake, today=date(2026, 9, 28)).history("X:GER", HistoryPeriod.D1)

    assert fake.history_days == [5]
    assert hist.candles == [friday]
    assert str(hist.security.symbol) == "X:GER"


def test_history_d1_without_candles_is_empty() -> None:
    hist = _source(FakeScraper()).history("X:GER", HistoryPeriod.D1)
    assert hist.candles == []


# 6. A target price of zero or less is rejected before any fetch


@pytest.mark.parametrize("price", [0, 0.0, -1.5])
def test_validate_rejects_non_positive_price(price: float) -> None:
    fake = FakeScraper(candles=[OHLCV(date=datetime(2026, 9, 1), close=100.0)])
    with pytest.raises(ValueError, match="greater than 0"):
        _source(fake).validate("X:GER", date(2026, 9, 1), price)
    assert fake.history_days == []


def test_resolve_rejects_non_positive_price() -> None:
    fake = FakeScraper([Security(symbol="X:GER", name="X")])
    criteria = SecurityQuery(symbol="X", price_on=PriceOnDate(price=0, date=date(2026, 9, 1)))
    with pytest.raises(ValueError, match="greater than 0"):
        _source(fake).resolve(criteria)
    assert fake.history_days == []
