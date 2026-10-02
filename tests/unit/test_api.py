"""FTDataSource against an injected fake Scraper (no network, no patching)"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime

import pytest
from pydantic_market_data.models import (
    OHLCV,
    History,
    HistoryPeriod,
    Price,
    PriceOnDate,
    PriceVerificationError,
    Security,
    SecurityQuery,
    Symbol,
)

from ftmarkets.api import FTDataSource
from ftmarkets.extract.scraper import Scraper


class FakeScraper(Scraper):
    """Answers search() from ``results`` by query (no results for any other query) and
    get_history() with ``candles``; records every query and every history request"""

    def __init__(
        self,
        results: Mapping[str, list[Security]] | None = None,
        candles: Sequence[OHLCV] = (),
    ) -> None:
        self.results = dict(results or {})
        self.candles = list(candles)
        self.searched: list[str] = []
        self.history_requests: list[tuple[str, int]] = []

    def search(self, query: Symbol.Input) -> list[Security]:
        query_str = str(query)
        self.searched.append(query_str)
        return list(self.results.get(query_str, []))

    def get_history(self, symbol: Symbol.Input, days: int = 30) -> History:
        symbol_val = Symbol(root=symbol) if isinstance(symbol, str) else symbol
        self.history_requests.append((symbol_val.root, days))
        return History(
            security=Security(symbol=symbol_val, name=symbol_val.root), candles=self.candles
        )


def _source(fake: FakeScraper) -> FTDataSource:
    return FTDataSource(scraper_instance=fake)


APPLE = Security(symbol="AAPL", name="Apple")


def test_search():
    results = _source(FakeScraper({"AAPL": [APPLE]})).search("AAPL")
    assert [r.symbol.root for r in results] == ["AAPL"]


def test_resolve_basic():
    fake = FakeScraper({"AAPL": [Security(symbol="AAPL:NSQ", name="Apple Inc", currency="USD")]})

    result = _source(fake).resolve(SecurityQuery(symbol="AAPL"))

    assert result is not None
    assert result.symbol.root == "AAPL:NSQ"
    assert fake.searched == ["AAPL"]


def test_resolve_isin():
    fake = FakeScraper(
        {"US0378331005": [Security(symbol="AAPL", name="Apple", isin="US0378331005")]}
    )
    res = _source(fake).resolve(SecurityQuery(isin="US0378331005"))
    assert res is not None
    assert res.symbol.root == "AAPL"


def test_resolve_currency_filter():
    fake = FakeScraper(
        {
            "AAPL": [
                Security(symbol="AAPL:EUR", name="Apple EUR", currency="EUR"),
                Security(symbol="AAPL:USD", name="Apple USD", currency="USD"),
            ]
        }
    )
    res = _source(fake).resolve(SecurityQuery(symbol="AAPL", currency="USD"))
    assert res is not None
    assert res.symbol.root == "AAPL:USD"


def test_resolve_price_validation():
    fake = FakeScraper(
        {"AAPL": [APPLE]},
        candles=[OHLCV(date=datetime(2023, 1, 1), open=149.0, high=151.0, low=148.0, close=150.0)],
    )
    criteria = SecurityQuery(
        symbol="AAPL", price_on=PriceOnDate(price=150.0, date=date(2023, 1, 1))
    )

    res = _source(fake).resolve(criteria)

    assert res is not None
    assert res.symbol.root == "AAPL"
    assert [symbol for symbol, _ in fake.history_requests] == ["AAPL"]


def test_resolve_price_validation_fail():
    fake = FakeScraper({"AAPL": [APPLE]}, candles=[OHLCV(date=datetime(2023, 1, 1), close=200.0)])
    criteria = SecurityQuery(
        symbol="AAPL", price_on=PriceOnDate(price=150.0, date=date(2023, 1, 1))
    )
    assert _source(fake).resolve(criteria) is None


def _two_day_candles() -> list[OHLCV]:
    return [
        OHLCV(date=datetime(2023, 1, 15), open=100, high=105, low=95, close=100),
        OHLCV(date=datetime(2023, 3, 15), open=200, high=205, low=195, close=200),
    ]


def test_resolve_price_validation_all_points_match():
    fake = FakeScraper(
        {"VALID": [Security(symbol="VALID:EX", name="Valid Ticker")]}, candles=_two_day_candles()
    )
    criteria = SecurityQuery(
        symbol="VALID",
        price_on=[
            PriceOnDate(price=200.0, date=datetime(2023, 3, 15)),
            PriceOnDate(price=100.0, date=datetime(2023, 1, 15)),
        ],
    )

    result = _source(fake).resolve(criteria)

    assert result is not None
    assert result.symbol.root == "VALID:EX"
    # One history fetch covers every price point
    assert len(fake.history_requests) == 1


def test_resolve_price_validation_one_point_mismatch_rejects():
    fake = FakeScraper(
        {"VALID": [Security(symbol="VALID:EX", name="Valid Ticker")]}, candles=_two_day_candles()
    )
    criteria = SecurityQuery(
        symbol="VALID",
        price_on=[
            PriceOnDate(price=100.0, date=datetime(2023, 1, 15)),
            PriceOnDate(price=500.0, date=datetime(2023, 3, 15)),
        ],
    )
    assert _source(fake).resolve(criteria) is None


def test_ensure_datetime_fail_fast():
    ds = _source(FakeScraper())
    with pytest.raises(TypeError):
        ds._ensure_datetime("invalid-date")  # type: ignore

    with pytest.raises(TypeError):
        ds._ensure_datetime(12345)  # type: ignore


def test_ensure_datetime_none():
    assert isinstance(_source(FakeScraper())._ensure_datetime(None), datetime)


def test_validate():
    ds = _source(FakeScraper(candles=[OHLCV(date=datetime(2023, 1, 1), close=150.0)]))
    assert ds.validate("AAPL", datetime(2023, 1, 1), Price(root=150.0)) is True
    assert ds.validate(Symbol(root="AAPL"), datetime(2023, 1, 1), Price(root=150.0)) is True
    with pytest.raises(PriceVerificationError):
        ds.validate("AAPL", datetime(2023, 1, 1), Price(root=200.0))


def test_validate_against_daily_range():
    ds = _source(
        FakeScraper(
            candles=[OHLCV(date=datetime(2023, 1, 15), open=100, high=105, low=95, close=100)]
        )
    )
    assert ds.validate(Symbol(root="T:EX"), datetime(2023, 1, 15), Price(root=100.0)) is True
    # Outside the day's range and beyond the close tolerance
    with pytest.raises(PriceVerificationError):
        ds.validate(Symbol(root="T:EX"), datetime(2023, 1, 15), Price(root=150.0))


def test_validate_float_price():
    ds = _source(FakeScraper(candles=[OHLCV(date=datetime(2023, 1, 1), close=150.0)]))
    assert ds.validate("AAPL", datetime(2023, 1, 1), 150.0) is True


def test_check_price_match_boundaries():
    ds = _source(FakeScraper())
    history = History(
        security=APPLE,
        candles=[
            OHLCV(date=datetime(2023, 1, 1), open=140.0, high=155.0, low=145.0, close=150.0),
            OHLCV(date=datetime(2023, 1, 5), open=100.0, high=110.0, low=90.0, close=100.0),
        ],
    )
    # Match within high/low range
    assert ds._check_price_match(history, datetime(2023, 1, 1), Price(root=146.0))
    # Match outside range but within 5% of close
    assert ds._check_price_match(history, datetime(2023, 1, 1), Price(root=144.0))
    # Outside 5% of close and outside range
    with pytest.raises(PriceVerificationError):
        ds._check_price_match(history, datetime(2023, 1, 1), Price(root=100.0))
    # Target within 5 days (nearest match)
    assert ds._check_price_match(history, datetime(2023, 1, 3), Price(root=150.0))
    # Target beyond 5 days (no match)
    assert not ds._check_price_match(history, datetime(2023, 1, 11), Price(root=100.0))


def test_resolve_description():
    fake = FakeScraper({"Apple Inc": [APPLE]})
    res = _source(fake).resolve(SecurityQuery(description="Apple Inc"))
    assert res is not None
    assert res.symbol.root == "AAPL"
    assert fake.searched == ["Apple Inc"]


def test_resolve_no_candidates():
    fake = FakeScraper()
    assert _source(fake).resolve(SecurityQuery(symbol="UNKNOWN")) is None
    assert fake.searched == ["UNKNOWN"]


def test_resolve_no_filtered_candidates():
    # Currency mismatch
    fake = FakeScraper({"AAPL": [Security(symbol="AAPL:EUR", name="Apple EUR", currency="EUR")]})
    assert _source(fake).resolve(SecurityQuery(symbol="AAPL", currency="USD")) is None


def test_get_price_exact():
    ds = _source(FakeScraper(candles=[OHLCV(date=datetime(2023, 1, 1), close=150.0)]))
    assert ds.get_price("AAPL", date(2023, 1, 1)) == Price(root=150.0)


def test_get_price_nearest():
    ds = _source(
        FakeScraper(
            candles=[
                OHLCV(date=datetime(2023, 1, 1), close=140.0),  # 2 days off
                OHLCV(date=datetime(2023, 1, 2), close=145.0),  # 1 day off
            ]
        )
    )
    assert ds.get_price("AAPL", date(2023, 1, 3)) == Price(root=145.0)


def test_get_price_failure():
    with pytest.raises(ValueError):
        _source(FakeScraper()).get_price("AAPL", date(2023, 1, 1))


def test_history():
    fake = FakeScraper()
    res = _source(fake).history("AAPL", HistoryPeriod.D5)
    assert fake.history_requests == [("AAPL", 7)]
    assert res.security.symbol.root == "AAPL"


def test_resolve_figi_searched_before_isin():
    fake = FakeScraper({"BBG000B9XRY4": [APPLE], "US0378331005": [APPLE]})

    res = _source(fake).resolve(SecurityQuery(figi="BBG000B9XRY4", isin="US0378331005"))

    assert res is not None
    assert fake.searched == ["BBG000B9XRY4"]


def test_resolve_figi_falls_back_to_isin():
    fake = FakeScraper({"US0378331005": [APPLE]})

    res = _source(fake).resolve(SecurityQuery(figi="BBG000B9XRY4", isin="US0378331005"))

    assert res is not None
    assert fake.searched == ["BBG000B9XRY4", "US0378331005"]


def test_resolve_figi_only():
    fake = FakeScraper({"BBG000B9XRY4": [APPLE]})

    res = _source(fake).resolve(SecurityQuery(figi="BBG000B9XRY4"))

    assert res is not None
    assert fake.searched == ["BBG000B9XRY4"]
