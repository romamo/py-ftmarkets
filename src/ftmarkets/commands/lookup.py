import json
import logging
import sys

import requests
from pydantic_market_data.cli_models import SearchArgs
from pydantic_market_data.models import Price, PriceVerificationError, StrictDate, Symbol

from .. import api
from ..extract.scraper import ScraperError
from ..utils import parse_date

logger = logging.getLogger(__name__)


class LookupCommand(SearchArgs):
    """Lookup a ticker symbol"""

    def cli_cmd(self) -> None:
        ds = api.FTDataSource()

        # Priority: ISIN > Symbol > Description
        query = self.isin or self.ticker or self.desc
        if not query:
            logger.error("Please provide --isin, --ticker, or --desc")
            sys.exit(1)

        results = ds.search(query)

        # Filter results
        filtered = []
        for s in results:
            if self.currency and (
                not s.currency or str(s.currency).upper() != str(self.currency).upper()
            ):
                continue
            if self.country and (
                not s.country or str(s.country).upper() != str(self.country).upper()
            ):
                continue
            if self.asset_class and (
                not s.asset_class or str(s.asset_class).upper() != str(self.asset_class).upper()
            ):
                continue
            if self.exchange and (
                not s.exchange or self.exchange.lower() not in s.exchange.lower()
            ):
                continue
            filtered.append(s)

        # Price/Date Validation
        if self.price:
            if not self.date:
                logger.error("Date is required for price validation")
                sys.exit(1)

            target_dt = parse_date(self.date)
            if not target_dt:
                logger.error("Invalid date format")
                sys.exit(1)

            target_price = Price(root=self.price)
            strict_date = StrictDate(root=target_dt)

            validated_count = 0
            limit = self.limit if self.limit is not None else 100

            for s in filtered:
                if limit > 0 and validated_count >= limit:
                    break

                # Removed bare except Exception to enforce Fail Fast rule.
                # Let it crash if not BaseScraperError.
                try:
                    if ds.validate(s.ticker, strict_date.value, target_price):
                        self._print_result(s)
                        validated_count += 1
                except PriceVerificationError as e:
                    # Clean up the Matched range format
                    range_str = (
                        f"{e.actual_low:.2f} - {e.actual_high:.2f}"
                        if e.actual_low is not None and e.actual_high is not None
                        else str(e)
                    )
                    logger.info(f"Validation failed for {s.ticker}, Matched range {range_str}")
                except ScraperError as e:
                    logger.debug(f"Scraper error during validation of {s.ticker}: {e}")
                except requests.exceptions.HTTPError as e:
                    logger.debug(f"HTTP error during validation of {s.ticker}: {e}")

            if validated_count == 0:
                logger.error("Ticker not found")
                sys.exit(1)
            return

        limit = self.limit if self.limit is not None else 100
        symbols = filtered[:limit] if limit > 0 else filtered

        if not symbols:
            logger.error("Ticker not found")
            sys.exit(1)

        if self.format == "json":
            data = [s.model_dump(mode="json") for s in symbols]
            print(json.dumps(data, indent=2))
        elif self.format == "xml":
            print("<Results>")
            for s in symbols:
                self._print_xml_symbol(s)
            print("</Results>")
        else:
            for s in symbols:
                print(s.ticker)

    def _print_result(self, s: Symbol) -> None:
        if self.format == "json":
            print(json.dumps(s.model_dump(mode="json"), indent=2))
        elif self.format == "xml":
            self._print_xml_symbol(s)
        else:
            print(s.ticker)

    def _print_xml_symbol(self, s: Symbol) -> None:
        print("  <Symbol>")
        print(f"    <Ticker>{s.ticker}</Ticker>")
        print(f"    <Name>{s.name}</Name>")
        print(f"    <Exchange>{s.exchange or ''}</Exchange>")
        print(f"    <Country>{s.country or ''}</Country>")
        print(f"    <Currency>{s.currency or ''}</Currency>")
        print(f"    <AssetClass>{s.asset_class or ''}</AssetClass>")
        print("  </Symbol>")
