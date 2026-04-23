import logging
import sys

from pydantic_market_data.cli_models import HistoryArgs
from pydantic_market_data.models import (
    HistoryPeriod,
    Price,
    PriceOnDate,
    PriceVerificationError,
    StrictDate,
)

from .. import api
from ..utils import parse_date

logger = logging.getLogger(__name__)


class HistoryCommand(HistoryArgs):
    """Fetch history and validate"""

    def cli_cmd(self) -> None:
        ds = api.FTDataSource()
        target_dt = parse_date(self.date) if self.date else None

        target_date_vo = StrictDate(root=target_dt) if target_dt else None
        target_price_vo = Price(root=self.price) if self.price else None

        price_on = (
            PriceOnDate(price=target_price_vo, date=target_date_vo.root)
            if target_price_vo and target_date_vo
            else None
        )
        criteria = api.SecurityQuery(
            isin=self.isin,
            symbol=self.symbol,
            description=self.desc,
            price_on=price_on,
        )

        sym = ds.resolve(criteria)

        if not sym:
            logger.error("Could not resolve symbol.")
            sys.exit(1)

        symbol = sym.symbol
        print(f"Resolved to: {symbol}")

        # safely parse HistoryPeriod
        try:
            enum_period = HistoryPeriod(self.period)
        except ValueError:
            valid_periods = [p.value for p in HistoryPeriod]
            logger.error(f"Invalid period '{self.period}'. Valid: {valid_periods}")
            sys.exit(1)

        hist = ds.history(symbol, period=enum_period)
        df = hist.to_pandas()

        if df.empty:
            logger.error("No history found.")
            sys.exit(1)

        if self.format == "json":
            print(hist.model_dump_json(indent=2))
        else:
            print(df.tail())

        if self.price and self.date:
            target_dt = parse_date(self.date)
            if not target_dt:
                logger.error("Invalid date: %s", self.date)
                sys.exit(1)

            try:
                if ds.validate(symbol, target_dt, Price(root=self.price)):
                    print("VALIDATION PASSED")
                else:
                    logger.error("VALIDATION FAILED")
                    sys.exit(1)
            except PriceVerificationError as e:
                logger.error(f"VALIDATION FAILED: {e}")
                sys.exit(1)
