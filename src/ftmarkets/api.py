import logging
from collections.abc import Callable
from datetime import date, datetime

from pydantic_market_data.interfaces import DataSource
from pydantic_market_data.models import (
    OHLCV,
    History,
    HistoryPeriod,
    Price,
    PriceVerificationError,
    Security,
    SecurityQuery,
    StrictDate,
    Symbol,
)

from .extract.scraper import Scraper, scraper

# Re-export needed models for CLI
__all__ = ["FTDataSource", "History", "OHLCV", "SecurityQuery", "Security"]

logger = logging.getLogger(__name__)

_PRICE_LOOKUP_WINDOW_DAYS = 5  # Covers weekends + public holidays

# Calendar days fetched per period; YTD depends on today and is computed in history()
_PERIOD_DAYS: dict[HistoryPeriod, int] = {
    # D1 fetches a few days and keeps the last candle, so a Monday or a day after a
    # holiday still has one
    HistoryPeriod.D1: 5,
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


class FTDataSource(DataSource):
    """
    Financial Times (markets.ft.com) data source implementation.
    Delegates to strict Scraper.
    """

    def __init__(
        self,
        scraper_instance: Scraper | None = None,
        today: Callable[[], date] = date.today,
    ):
        self.scraper = scraper_instance or scraper
        self.today = today

    def search(self, query: str) -> list[Security]:
        return self.scraper.search(query)

    def resolve(self, criteria: SecurityQuery) -> Security | None:
        """
        Resolve a security based on criteria.
        Checks for FIGI (preferred), ISIN, Symbol, Description.
        With a symbol as well, FIGI and ISIN hits count only when their symbol matches
        it (see ``_matching_symbol``); else the symbol itself is searched.
        Validates against Price/Date if provided.
        """
        symbol = (
            Symbol(root=criteria.symbol) if isinstance(criteria.symbol, str) else criteria.symbol
        )
        candidates: list[Security] = []
        for identifier in (criteria.figi, criteria.isin):
            if candidates or not identifier:
                continue
            candidates = self.scraper.search(str(identifier))
            if symbol:
                candidates = self._matching_symbol(candidates, symbol)
        if not candidates and criteria.symbol:
            candidates = self.scraper.search(str(criteria.symbol))
        if not candidates and criteria.description:
            candidates = self.scraper.search(str(criteria.description))

        if not candidates:
            return None

        if criteria.asset_class is not None:
            candidates = [c for c in candidates if c.asset_class == criteria.asset_class]

        if criteria.exchange:
            wanted = criteria.exchange.lower()
            candidates = [c for c in candidates if c.exchange and wanted in c.exchange.lower()]

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
                        cand.symbol,
                        cand_curr,
                        criteria.currency,
                    )
                    continue

            filtered.append(cand)

        if not filtered:
            return None

        # Price validation
        if criteria.price_on:
            # A candidate must match every price point; one history fetch covers them all.
            targets = [
                (
                    self._ensure_datetime(point.date),
                    self._positive_price(point.price),
                )
                for point in criteria.price_on
            ]
            days = self._get_required_history_days(min(dt for dt, _ in targets))

            for cand in filtered:
                hist = self.scraper.get_history(cand.symbol, days=days)
                try:
                    if all(self._check_price_match(hist, dt, pr) for dt, pr in targets):
                        return cand
                except PriceVerificationError as e:
                    logger.debug("Candidate %s failed validation: %s", cand.symbol, e)

            return None

        return filtered[0]

    @staticmethod
    def _matching_symbol(candidates: list[Security], symbol: Symbol) -> list[Security]:
        """
        The candidates whose FT symbol (``TICKER:EXCH[:CCY]``) matches ``symbol``,
        ignoring case: exact matches if any, else those that ``symbol``'s parts begin,
        so ``4GLD:LSE`` matches ``4GLD:LSE:GBX`` (but ``4GLD:LSE:GBX`` never matches
        ``4GLD:LSE``, and ``4GLD:L`` never matches ``4GLD:LSE``).
        """
        wanted = str(symbol).upper().split(":")
        parts = [(c, str(c.symbol).upper().split(":")) for c in candidates]
        exact = [c for c, p in parts if p == wanted]
        if exact:
            return exact
        return [c for c, p in parts if p[: len(wanted)] == wanted]

    def get_price(self, symbol: Symbol.Input, date: date | None = None) -> Price:
        """
        Get the price for a symbol (current or historical).
        """
        symbol_val = Symbol(root=symbol) if isinstance(symbol, str) else symbol
        target_dt = self._ensure_datetime(date)
        days = self._get_required_history_days(target_dt)
        hist = self.scraper.get_history(symbol_val, days=days + 10)

        target_date = target_dt.date()
        match_range = self._find_nearest_candle(hist, target_date)

        if match_range and match_range.close is not None:
            return Price(root=float(match_range.close))

        raise ValueError(
            f"Could not retrieve price for symbol '{symbol_val.root}' on {target_date}"
        )

    def history(self, symbol: Symbol.Input, period: HistoryPeriod = HistoryPeriod.MO1) -> History:
        symbol_val = Symbol(root=symbol) if isinstance(symbol, str) else symbol
        if period == HistoryPeriod.YTD:
            today = self.today()
            # Days since 1 January, counting today
            days = (today - date(today.year, 1, 1)).days + 1
        else:
            days = _PERIOD_DAYS[period]
        hist = self.scraper.get_history(symbol_val, days=days)
        if period == HistoryPeriod.D1:
            latest = sorted(hist.candles, key=lambda c: c.date)[-1:]
            return History(security=hist.security, candles=latest)
        return hist

    def validate(
        self,
        symbol: Symbol.Input,
        target_date: date,
        target_price: Price.Input,
        price_tolerance: float = 0.10,
    ) -> bool:
        """
        Validates if the symbol traded near the target price on the target date.
        """
        symbol_val = Symbol(root=symbol) if isinstance(symbol, str) else symbol
        price_val = self._positive_price(target_price)

        target_dt = self._ensure_datetime(target_date)
        days = self._get_required_history_days(target_dt)
        hist = self.scraper.get_history(symbol_val, days=days + 10)

        return self._check_price_match(hist, target_dt, price_val, price_tolerance)

    # --- Internal Helpers ---

    @staticmethod
    def _positive_price(price: Price.Input) -> Price:
        """``price`` as a Price; a price check against zero or less is meaningless"""
        price_val = Price(root=float(price)) if isinstance(price, (int, float)) else price
        if price_val.root <= 0:
            raise ValueError(f"target price must be greater than 0, got {price_val.root}")
        return price_val

    def _ensure_datetime(self, date_input: StrictDate.Input | None = None) -> datetime:
        if date_input is None:
            return datetime.now()
        d = date_input.root if isinstance(date_input, StrictDate) else date_input
        return datetime.combine(d, datetime.min.time())

    def _get_required_history_days(self, target_date: datetime) -> int:
        days_diff = (datetime.now() - target_date).days
        return max(days_diff + 5, 30)

    def _find_nearest_candle(self, history: History, target_date: date) -> OHLCV | None:
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
        self,
        history: History,
        target_dt: datetime,
        target_price: Price,
        price_tolerance: float = 0.10,
    ) -> bool:
        target_date = target_dt.date()
        target_price_val = target_price.root

        match_range = self._find_nearest_candle(history, target_date)

        if not match_range:
            return False

        logger.info(
            "Matched range for %s on %s: Low=%s, High=%s, Close=%s",
            history.security.symbol,
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

        # Close Check (tolerance)
        if close is not None:
            pct_diff = abs(close - target_price_val) / target_price_val
            if pct_diff < price_tolerance:
                return True

        # If we reach here, it failed. Raise error with details.
        tol_pct = int(price_tolerance * 100)
        msg = (
            f"Price {target_price_val} is outside daily range"
            if low is not None and high is not None
            else f"Price {target_price_val} is not within {tol_pct}% of close"
        )
        raise PriceVerificationError(
            msg,
            symbol=str(history.security.symbol),
            actual_date=target_date,
            expected_price=target_price_val,
            actual_low=low,
            actual_high=high,
            actual_close=close,
            source="Financial Times",
        )
