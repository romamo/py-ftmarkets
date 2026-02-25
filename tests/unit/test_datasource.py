from datetime import datetime
from unittest.mock import MagicMock

import pytest
from pydantic_market_data.models import (
    OHLCV,
    History,
    Price,
    PriceVerificationError,
    SecurityCriteria,
    Symbol,
    Ticker,
)

from ftmarkets.api import FTDataSource
from ftmarkets.extract.scraper import Scraper


@pytest.fixture
def mock_scraper():
    return MagicMock(spec=Scraper)


@pytest.fixture
def datasource(mock_scraper):
    return FTDataSource(scraper_instance=mock_scraper)


def test_resolve_basic(datasource, mock_scraper):
    mock_scraper.search.return_value = [Symbol(ticker="AAPL:NSQ", name="Apple Inc", currency="USD")]

    criteria = SecurityCriteria(symbol="AAPL")
    result = datasource.resolve(criteria)

    assert result is not None
    assert result.ticker.root == "AAPL:NSQ"
    mock_scraper.search.assert_called_with("AAPL")


def test_resolve_currency_filter(datasource, mock_scraper):
    mock_scraper.search.return_value = [
        Symbol(ticker="TEST:EUR", name="Test Eur", currency="EUR"),
        Symbol(ticker="TEST:USD", name="Test Usd", currency="USD"),
    ]

    # Filter for USD
    criteria = SecurityCriteria(symbol="TEST", currency="USD")
    result = datasource.resolve(criteria)

    assert result is not None
    assert result.ticker.root == "TEST:USD"


def test_resolve_price_validation(datasource, mock_scraper):
    mock_scraper.search.return_value = [
        Symbol(ticker="VALID:EX", name="Valid Ticker", currency="USD")
    ]

    # Mock history with target price
    target_date = datetime(2023, 1, 15)
    mock_scraper.get_history.return_value = History(
        symbol=Symbol(ticker="VALID:EX", name="Valid Ticker"),
        candles=[
            OHLCV(date=datetime(2023, 1, 15), open=100, high=105, low=95, close=100, volume=1000)
        ],
    )

    criteria = SecurityCriteria(
        symbol="VALID", target_price=Price(root=100.0), target_date=target_date
    )

    result = datasource.resolve(criteria)
    assert result is not None
    assert result.ticker.root == "VALID:EX"

    # Verify days calculation logic called get_history
    mock_scraper.get_history.assert_called_once()


def test_validate_logic(datasource, mock_scraper):
    target_date = datetime(2023, 1, 15)
    mock_scraper.get_history.return_value = History(
        symbol=Symbol(ticker="T:EX", name="T"),
        candles=[OHLCV(date=datetime(2023, 1, 15), open=100, high=105, low=95, close=100)],
    )

    # Valid
    assert datasource.validate(Ticker(root="T:EX"), target_date, Price(root=100.0)) is True
    # Invalid (out of range/mismatch) - now raises per Fail Fast
    with pytest.raises(PriceVerificationError):
        datasource.validate(Ticker(root="T:EX"), target_date, Price(root=150.0))
