from pydantic_market_data.models import History

from ftmarkets import api


def test_search():
    results = api.search("AAPL")
    assert len(results) > 0
    assert any("Apple" in r.name for r in results)


def test_resolve_ticker():
    # Test resolution by symbol
    results = api.resolve_ticker(symbol="AAPL")
    assert len(results) > 0
    assert results[0].ticker == "AAPL:NSQ"


def test_history():
    # Test fetching history
    ticker = "AAPL:NSQ"
    hist = api.history(ticker, period="1mo")
    assert isinstance(hist, History)
    assert len(hist.candles) > 0

    # Verify pandas conversion
    df = hist.to_pandas()
    assert not df.empty
    assert "Close" in df.columns


def test_datasource_interface():
    # Verify FTDataSource implementation
    ds = api.FTDataSource()
    results = ds.search("AAPL")
    assert len(results) > 0

    ticker = ds.resolve(api.SecurityCriteria(symbol="AAPL"))
    assert ticker.ticker == "AAPL:NSQ"


def test_resolve_with_price_validation():
    # Test resolution with price and date validation (Xetra-Gold)
    # Price on 2024-01-02 was around 60.50
    results = api.resolve_ticker(
        isin="DE000A0S9GB0", target_price=60.50, target_date="2024-01-02", currency="EUR"
    )
    assert len(results) > 0
    assert "4GLD" in results[0].ticker


def test_validate_older_date():
    # Test revalidation for a trade from Oct 2025 (more than 100 days ago)
    ds = api.FTDataSource()
    # LU1900066033: AMUNDI MSCI SEMICONDUCTORS E (CHIP:PAR:EUR)
    # Price on 2025-10-16 was around 68.50
    # Note: Using date object to test that path as well
    from datetime import date
    target_date = date(2025, 10, 16)
    target_price = 68.50
    ticker = "CHIP:PAR:EUR"
    
    is_valid = ds.validate(ticker, target_date, target_price)
    assert is_valid is True
