import logging
from datetime import date, datetime

from pydantic_market_data.interfaces import DataSource
from pydantic_market_data.models import (
    OHLCV,
    History,
    HistoryPeriod,
    Price,
    PriceVerificationError,
    SecurityCriteria,
    StrictDate,
    Symbol,
    Ticker,
)

from .extract.scraper import Scraper, scraper

# Re-export needed models for CLI
__all__ = ["FTDataSource", "History", "OHLCV", "SecurityCriteria", "Symbol"]

logger = logging.getLogger(__name__)

_PRICE_LOOKUP_WINDOW_DAYS = 5  # Covers weekends + public holidays


class FTDataSource(DataSource):
    """
    Financial Times (markets.ft.com) data source implementation.
    Delegates to strict Scraper.
    """

    def __init__(self, scraper_instance: Scraper | None = None):
        self.scraper = scraper_instance or scraper

    def search(self, query: str) -> list[Symbol]:
        return self.scraper.search(query)

    def resolve(self, criteria: SecurityCriteria) -> Symbol | None:
        """
        Resolve a security based on criteria.
        Checks for ISIN, Symbol, Description.
        Validates against Price/Date if provided.
        """
        candidates: list[Symbol] = []
        if criteria.isin:
            candidates = self.scraper.search(str(criteria.isin))
        if not candidates and criteria.symbol:
            candidates = self.scraper.search(str(criteria.symbol))
        if not candidates and criteria.description:
            candidates = self.scraper.search(str(criteria.description))

        if not candidates:
            return None

        filtered = []
        for cand in candidates:
            # Currency Check
            if criteria.currency:
                cand_curr = str(cand.currency).upper() if cand.currency else None
                if cand_curr != str(criteria.currency).upper():
                    logger.debug(
                        "Skipping candidate %s due to currency mismatch: %s != %s",
                        cand.ticker,
                        cand_curr,
                        criteria.currency,
                    )
                    continue

            filtered.append(cand)

        if not filtered:
            return None

        # Price validation
        if criteria.target_price:
            target_dt = self._ensure_datetime(criteria.target_date)
            days = self._get_required_history_days(target_dt)

            tp = criteria.target_price
            target_pr = Price(root=float(tp)) if isinstance(tp, (int, float)) else tp

            valid_candidates = []
            for cand in filtered:
                hist = self.scraper.get_history(cand.ticker, days=days)
                try:
                    if self._check_price_match(hist, target_dt, target_pr):
                        valid_candidates.append(cand)
                except PriceVerificationError:
                    continue

            if valid_candidates:
                return valid_candidates[0]
            return None

        return filtered[0]

    def get_price(self, ticker: Ticker | str, date: date | None = None) -> Price:
        """
        Get the price for a ticker (current or historical).
        """
        ticker_val = Ticker(root=ticker) if isinstance(ticker, str) else ticker
        target_dt = self._ensure_datetime(date)
        days = self._get_required_history_days(target_dt)
        hist = self.scraper.get_history(ticker_val, days=days + 10)

        target_date = target_dt.date()
        match_range = self._find_nearest_candle(hist, target_date)

        if match_range and match_range.close is not None:
            return Price(root=float(match_range.close))

        raise RuntimeError(
            f"Could not retrieve price for ticker '{ticker_val.root}' on {target_date}"
        )

    def history(self, ticker: Ticker | str, period: HistoryPeriod = HistoryPeriod.MO1) -> History:
        ticker_val = Ticker(root=ticker) if isinstance(ticker, str) else ticker
        # Convert period string to days
        days_map = {
            HistoryPeriod.D1: 2,
            HistoryPeriod.D5: 7,
            HistoryPeriod.MO1: 30,
            HistoryPeriod.MO3: 90,
            HistoryPeriod.MO6: 180,
            HistoryPeriod.Y1: 365,
            HistoryPeriod.Y2: 365 * 2,
            HistoryPeriod.Y5: 365 * 5,
            HistoryPeriod.Y10: 365 * 10,
            HistoryPeriod.MAX: 365 * 20,
        }

        days = days_map.get(period, 30)
        return self.scraper.get_history(ticker_val, days=days)

    def validate(
        self, ticker: Ticker | str, target_date: date, target_price: Price | float
    ) -> bool:
        """
        Validates if the ticker traded near the target price on the target date.
        """
        ticker_val = Ticker(root=ticker) if isinstance(ticker, str) else ticker
        if isinstance(target_price, (int, float)):
            price_val = Price(root=float(target_price))
        else:
            price_val = target_price

        target_dt = self._ensure_datetime(target_date)
        days = self._get_required_history_days(target_dt)
        # Fetch slightly more history to be safe
        hist = self.scraper.get_history(ticker_val, days=days + 10)

        return self._check_price_match(hist, target_dt, price_val)

    # --- Internal Helpers ---

    def _ensure_datetime(self, date_input: StrictDate.Input | None = None) -> datetime:
        if date_input is None:
            return datetime.now()
        d = date_input.root if isinstance(date_input, StrictDate) else date_input
        return datetime.combine(d, datetime.min.time())

    def _get_required_history_days(self, target_date: datetime) -> int:
        days_diff = (datetime.now() - target_date).days
        return max(days_diff + 5, 30)

    def _find_nearest_candle(self, history: History, target_date):
        """Return the OHLCV candle closest to target_date within _PRICE_LOOKUP_WINDOW_DAYS."""
        # Exact match first
        for c in history.candles:
            c_dt = c.date if isinstance(c.date, datetime) else None
            if c_dt and c_dt.date() == target_date:
                return c

        # Nearest within window
        candidates = []
        for c in history.candles:
            c_dt = c.date if isinstance(c.date, datetime) else None
            if c_dt:
                diff = abs((c_dt.date() - target_date).days)
                if diff <= _PRICE_LOOKUP_WINDOW_DAYS:
                    candidates.append((diff, c))

        if candidates:
            candidates.sort(key=lambda x: x[0])
            return candidates[0][1]
        return None

    def _check_price_match(
        self, history: History, target_dt: datetime, target_price: Price
    ) -> bool:
        target_date = target_dt.date()
        target_price_val = target_price.root

        match_range = self._find_nearest_candle(history, target_date)

        if not match_range:
            return False

        logger.info(
            "Matched range for %s on %s: Low=%s, High=%s, Close=%s",
            history.symbol.ticker,
            target_date,
            match_range.low,
            match_range.high,
            match_range.close,
        )

        # Range Check
        low = match_range.low
        high = match_range.high
        close = match_range.close

        if low is not None and high is not None:
            if low <= target_price_val <= high:
                return True

        # Close Check (5% tolerance)
        if close is not None:
            pct_diff = abs(close - target_price_val) / target_price_val
            if pct_diff < 0.05:
                return True

        # If we reach here, it failed. Raise error with details.
        raise PriceVerificationError(
            f"Price {target_price_val} does not match {history.symbol.ticker}",
            ticker=str(history.symbol.ticker),
            actual_date=target_date,
            expected_price=target_price_val,
            actual_low=low,
            actual_high=high,
            actual_close=close,
        )
