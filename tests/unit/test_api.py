import unittest
from datetime import date, datetime
from unittest.mock import MagicMock

from pydantic_market_data.models import (
    OHLCV,
    History,
    Price,
    PriceVerificationError,
    SecurityCriteria,
    Symbol,
)

from ftmarkets.api import FTDataSource
from ftmarkets.extract.scraper import Scraper


class TestFTDataSource(unittest.TestCase):
    def setUp(self):
        self.mock_scraper = MagicMock(spec=Scraper)
        self.ds = FTDataSource(scraper_instance=self.mock_scraper)

    def test_search(self):
        self.mock_scraper.search.return_value = [Symbol(ticker="AAPL", name="Apple")]
        results = self.ds.search("AAPL")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].ticker.root, "AAPL")

    def test_resolve_isin(self):
        criteria = SecurityCriteria(isin="US0378331005")
        self.mock_scraper.search.return_value = [
            Symbol(ticker="AAPL", name="Apple", isin="US0378331005")
        ]
        res = self.ds.resolve(criteria)
        self.assertIsNotNone(res)
        self.assertEqual(res.ticker.root, "AAPL")

    def test_resolve_currency_filter(self):
        criteria = SecurityCriteria(symbol="AAPL", currency="USD")
        self.mock_scraper.search.return_value = [
            Symbol(ticker="AAPL:EUR", name="Apple EUR", currency="EUR"),
            Symbol(ticker="AAPL:USD", name="Apple USD", currency="USD"),
        ]
        res = self.ds.resolve(criteria)
        self.assertIsNotNone(res)
        self.assertEqual(res.ticker.root, "AAPL:USD")

    def test_resolve_price_validation(self):
        target_date = date(2023, 1, 1)
        criteria = SecurityCriteria(
            symbol="AAPL", target_date=target_date, target_price=Price(root=150.0)
        )

        cand = Symbol(ticker="AAPL", name="Apple")
        self.mock_scraper.search.return_value = [cand]

        # Mock history showing match
        candles = [OHLCV(date=datetime(2023, 1, 1), open=149.0, high=151.0, low=148.0, close=150.0)]
        self.mock_scraper.get_history.return_value = History(symbol=cand, candles=candles)

        res = self.ds.resolve(criteria)
        self.assertIsNotNone(res)
        self.assertEqual(res.ticker.root, "AAPL")

    def test_ensure_datetime_fail_fast(self):
        with self.assertRaises(TypeError):
            self.ds._ensure_datetime("invalid-date")  # type: ignore

        with self.assertRaises(TypeError):
            self.ds._ensure_datetime(12345)  # type: ignore

    def test_validate(self):
        self.mock_scraper.get_history.return_value = History(
            symbol=Symbol(ticker="AAPL", name="Apple"),
            candles=[OHLCV(date=datetime(2023, 1, 1), close=150.0)],
        )
        self.assertTrue(self.ds.validate("AAPL", datetime(2023, 1, 1), Price(root=150.0)))
        with self.assertRaises(PriceVerificationError):
            self.ds.validate("AAPL", datetime(2023, 1, 1), Price(root=200.0))

    def test_check_price_match_boundaries(self):
        history = History(
            symbol=Symbol(ticker="AAPL", name="Apple"),
            candles=[
                OHLCV(date=datetime(2023, 1, 1), open=140.0, high=155.0, low=145.0, close=150.0),
                OHLCV(date=datetime(2023, 1, 5), open=100.0, high=110.0, low=90.0, close=100.0),
            ],
        )
        # Match within high/low range
        self.assertTrue(
            self.ds._check_price_match(history, datetime(2023, 1, 1), Price(root=146.0))
        )
        # Match outside range but within 5% of close
        self.assertTrue(
            self.ds._check_price_match(history, datetime(2023, 1, 1), Price(root=144.0))
        )
        # Outside 5% of close and outside range
        with self.assertRaises(PriceVerificationError):
            self.ds._check_price_match(history, datetime(2023, 1, 1), Price(root=100.0))
        # Target within 5 days (nearest match)
        self.assertTrue(
            self.ds._check_price_match(history, datetime(2023, 1, 3), Price(root=150.0))
        )
        # Target beyond 5 days (no match)
        self.assertFalse(
            self.ds._check_price_match(history, datetime(2023, 1, 11), Price(root=100.0))
        )

    def test_resolve_description(self):
        criteria = SecurityCriteria(description="Apple Inc")
        self.mock_scraper.search.return_value = [Symbol(ticker="AAPL", name="Apple")]
        res = self.ds.resolve(criteria)
        self.assertIsNotNone(res)
        self.assertEqual(res.ticker.root, "AAPL")
        self.mock_scraper.search.assert_called_with("Apple Inc")

    def test_resolve_no_candidates(self):
        self.mock_scraper.search.return_value = []
        criteria = SecurityCriteria(symbol="UNKNOWN")
        res = self.ds.resolve(criteria)
        self.assertIsNone(res)

    def test_resolve_no_filtered_candidates(self):
        # Currency mismatch
        criteria = SecurityCriteria(symbol="AAPL", currency="USD")
        self.mock_scraper.search.return_value = [
            Symbol(ticker="AAPL:EUR", name="Apple EUR", currency="EUR")
        ]
        res = self.ds.resolve(criteria)
        self.assertIsNone(res)

    def test_resolve_price_validation_fail(self):
        target_date = date(2023, 1, 1)
        criteria = SecurityCriteria(symbol="AAPL", target_date=target_date, target_price=150.0)
        cand = Symbol(ticker="AAPL", name="Apple")
        self.mock_scraper.search.return_value = [cand]
        # Mock history showing mismatch
        candles = [OHLCV(date=datetime(2023, 1, 1), close=200.0)]
        self.mock_scraper.get_history.return_value = History(symbol=cand, candles=candles)

        res = self.ds.resolve(criteria)
        self.assertIsNone(res)

    def test_get_price_exact(self):
        ticker = "AAPL"
        target_date = date(2023, 1, 1)
        cand = Symbol(ticker="AAPL", name="Apple")
        candles = [OHLCV(date=datetime(2023, 1, 1), close=150.0)]
        self.mock_scraper.get_history.return_value = History(symbol=cand, candles=candles)

        price = self.ds.get_price(ticker, target_date)
        self.assertEqual(price, Price(root=150.0))

    def test_get_price_nearest(self):
        ticker = "AAPL"
        target_date = date(2023, 1, 3)  # Jan 3
        cand = Symbol(ticker="AAPL", name="Apple")
        # Jan 1 and Jan 5 available
        candles = [
            OHLCV(date=datetime(2023, 1, 1), close=140.0),
            OHLCV(date=datetime(2023, 1, 5), close=160.0),
        ]
        self.mock_scraper.get_history.return_value = History(symbol=cand, candles=candles)

        # Should match Jan 1 (diff 2) over Jan 5 (diff 2) if same diff,
        # but let's make one closer
        candles = [
            OHLCV(date=datetime(2023, 1, 1), close=140.0),  # diff 2
            OHLCV(date=datetime(2023, 1, 2), close=145.0),  # diff 1
        ]
        self.mock_scraper.get_history.return_value = History(symbol=cand, candles=candles)
        price = self.ds.get_price(ticker, target_date)
        self.assertEqual(price, Price(root=145.0))

    def test_get_price_failure(self):
        ticker = "AAPL"
        target_date = date(2023, 1, 1)
        cand = Symbol(ticker="AAPL", name="Apple")
        self.mock_scraper.get_history.return_value = History(symbol=cand, candles=[])

        with self.assertRaises(RuntimeError):
            self.ds.get_price(ticker, target_date)

    def test_history(self):
        ticker = "AAPL"
        cand = Symbol(ticker="AAPL", name="Apple")
        self.mock_scraper.get_history.return_value = History(symbol=cand, candles=[])

        from pydantic_market_data.models import HistoryPeriod

        res = self.ds.history(ticker, HistoryPeriod.D5)
        self.mock_scraper.get_history.assert_called()
        self.assertEqual(res.symbol.ticker.root, "AAPL")
