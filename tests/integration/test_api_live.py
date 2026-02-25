from datetime import date

from pydantic_market_data.models import History, HistoryPeriod, SecurityCriteria
from pydantic_market_data.models import Price as PMDPrice

from ftmarkets import api
from ftmarkets.extract.schemas import Ticker


def test_search():
    ds = api.FTDataSource()
    results = ds.search("AAPL")
    assert len(results) > 0
    assert any("Apple" in r.name for r in results)


def test_resolve_ticker():
    # Test resolution by symbol
    ds = api.FTDataSource()
    sym = ds.resolve(SecurityCriteria(symbol="AAPL"))
    assert sym is not None
    assert str(sym.ticker) == "AAPL:NSQ"


def test_history():
    # Test fetching history
    ds = api.FTDataSource()
    ticker = Ticker(root="AAPL:NSQ")
    hist = ds.history(ticker, period=HistoryPeriod.MO1)
    assert isinstance(hist, History)
    assert len(hist.candles) > 0

    # Verify pandas conversion (History model supports it if pandas installed)
    try:
        df = hist.to_pandas()
        assert not df.empty
        assert "Close" in df.columns
    except ImportError:
        pass


def test_datasource_interface():
    # Verify FTDataSource implementation basic check
    ds = api.FTDataSource()
    results = ds.search("AAPL")
    assert len(results) > 0

    sym = ds.resolve(SecurityCriteria(symbol="AAPL"))
    assert sym is not None
    assert str(sym.ticker) == "AAPL:NSQ"


def test_resolve_with_price_validation():
    # Test resolution with price and date validation (Xetra-Gold)
    # Price on 2024-01-02 was around 60.50.
    # Must use date object or strictly parsed string.
    ds = api.FTDataSource()
    criteria = SecurityCriteria(
        isin="DE000A0S9GB0",
        target_price=PMDPrice(root=60.50),
        target_date=date(2024, 1, 2),
        currency="EUR",
    )
    sym = ds.resolve(criteria)
    assert sym is not None
    assert "4GLD" in str(sym.ticker)


def test_validate_older_date():
    # Test revalidation for a trade from Oct 2025 (future? wait 2026 is today)
    # Prompt says today is 2026. 2025-10-16 is past.
    ds = api.FTDataSource()
    # LU1900066033: AMUNDI MSCI SEMICONDUCTORS E (CHIP:PAR:EUR)
    # Price on 2025-10-16 was around 68.50
    target_date = date(2025, 10, 16)
    target_price = PMDPrice(root=68.50)
    ticker = Ticker(root="CHIP:PAR:EUR")

    is_valid = ds.validate(ticker, target_date, target_price)
    assert is_valid is True
