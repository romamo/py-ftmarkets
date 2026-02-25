import logging
import sys

from pydantic_market_data.cli_models import HistoryArgs
from pydantic_market_data.models import HistoryPeriod, Price, PriceVerificationError, StrictDate

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

        # Use SecurityCriteria to resolve
        criteria = api.SecurityCriteria(
            isin=self.isin,
            symbol=self.ticker,
            description=self.desc,
            target_price=target_price_vo,
            target_date=target_date_vo.root if target_date_vo else None,
        )

        sym = ds.resolve(criteria)

        if not sym:
            logger.error("Could not resolve ticker.")
            sys.exit(1)

        ticker = sym.ticker
        print(f"Resolved to: {ticker}")

        # safely parse HistoryPeriod
        try:
            enum_period = HistoryPeriod(self.period)
        except ValueError:
            valid_periods = [p.value for p in HistoryPeriod]
            logger.error(f"Invalid period '{self.period}'. Valid: {valid_periods}")
            sys.exit(1)

        hist = ds.history(ticker, period=enum_period)
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
                if ds.validate(ticker, target_dt, Price(root=self.price)):
                    print("VALIDATION PASSED")
                else:
                    logger.error("VALIDATION FAILED")
                    sys.exit(1)
            except PriceVerificationError as e:
                range_str = (
                    f"{e.actual_low:.2f} - {e.actual_high:.2f}"
                    if e.actual_low is not None and e.actual_high is not None
                    else str(e)
                )
                logger.error("VALIDATION FAILED (Matched range: %s)", range_str)
                sys.exit(1)
