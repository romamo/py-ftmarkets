import html as html_lib
import json
import logging
import re
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import requests
from lxml import html
from lxml.html import HtmlElement
from pydantic_extra_types.country import CountryAlpha2
from pydantic_extra_types.currency_code import Currency
from pydantic_market_data.models import OHLCV, History, Symbol

from ..client import FTClient, client
from .schemas import (
    ChartElementType,
    ChartRequest,
    ChartRequestElement,
    ChartResponse,
    ComponentSeries,
    DataPeriod,
    Isin,
    Ticker,
    Xid,
)

logger = logging.getLogger(__name__)


class ScraperError(Exception):
    """Scraper error."""


class Scraper:
    """
    Strictly typed scraper for FT Markets data.
    Encapsulates all logic for interacting with markets.ft.com.
    """

    def __init__(self, http_client: FTClient | None = None):
        self.client = http_client or client

    def search(self, query: str | Ticker) -> list[Symbol]:
        """
        Search for a security by ISIN, symbol, or name.
        Parsing logic is strict but resilient to HTML changes where possible.
        """
        query_str = str(query)
        url = "/data/search"
        response = self.client.get(url, params={"query": query_str})
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if response.status_code == 400:
                logger.debug("HTTP 400 Error for %s: %s", url, response.text)
            raise e

        tree = cast(HtmlElement, html.fromstring(response.content))

        # Check for direct redirect (tearsheet)
        if "tearsheet" in response.url:
            return self._parse_tearsheet_as_search_result(response.url, tree, query_str)

        # Standard search results page
        return self._parse_search_results(tree, query_str)

    def _parse_search_results(self, tree: HtmlElement, query: str) -> list[Symbol]:
        results: list[Symbol] = []
        # Mapping for FT tab IDs/names to standard types
        asset_class_map = {
            "etf-panel": "ETF",
            "equity-panel": "Equity",
            "fund-panel": "Fund",
            "index-panel": "Index",
            "ETFs": "ETF",
            "Equities": "Equity",
            "Funds": "Fund",
            "Indices": "Index",
            "Indicies": "Index",
            "etfs": "ETF",
            "equities": "Equity",
            "funds": "Fund",
            "indices": "Index",
        }

        xpath_query = (
            '//div[@role="tabpanel"] | //div[contains(@class, "mod-search-results__section")]'
        )
        panels = tree.xpath(xpath_query)

        # 1. Standard Panel Results
        for panel in panels:
            panel_id = panel.get("id")
            asset_type = asset_class_map.get(panel_id)
            if not asset_type:
                header = panel.xpath(".//h3")
                if header:
                    ft_name = header[0].text.strip()
                    asset_type = asset_class_map.get(ft_name, ft_name)

            rows = panel.xpath('.//table[contains(@class, "mod-ui-table")]/tbody/tr')
            for row in rows:
                cols = row.xpath("./td")
                if len(cols) >= 2:
                    name = cols[0].text_content().strip()
                    ticker_str = cols[1].text_content().strip()
                    exchange = cols[2].text_content().strip() if len(cols) > 2 else None
                    country = cols[3].text_content().strip() if len(cols) > 3 else None
                    self._add_to_results(
                        results, ticker_str, name, exchange, country, asset_type, query
                    )

        # 2. Capture ALL tearsheet links on the page (covers "Best Match" and other lists)
        # Avoid duplicates and ensure they look like tickers
        all_links = tree.xpath('//a[contains(@href, "tearsheet/summary?s=")]')
        for link in all_links:
            href = link.get("href")
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            ticker_str = qs.get("s", [None])[0]
            name = link.text_content().strip()
            if ticker_str and not any(str(r.ticker) == ticker_str for r in results):
                link_asset_type = None
                for at_key in ["equities", "etfs", "funds", "indices"]:
                    if f"/{at_key}/" in href:
                        link_asset_type = asset_class_map.get(at_key, at_key.capitalize())
                        break
                self._add_to_results(results, ticker_str, name, None, None, link_asset_type, query)

        return results

    def _add_to_results(
        self,
        results: list[Symbol],
        ticker: str,
        name: str,
        exchange: str | None,
        country: str | None,
        asset_type: str | None,
        query: str,
    ) -> None:
        country_code = self._map_country_to_code(country)
        currency = self._extract_currency(ticker) or self._map_country_to_currency(country_code)
        isin_val = query if self._is_isin(query) else None

        # pass raw strings to Symbol model
        sym = Symbol(
            ticker=ticker,
            name=name,
            exchange=exchange,
            country=cast(CountryAlpha2 | None, country_code),
            currency=currency,
            asset_class=asset_type,
            isin=str(isin_val) if isin_val else None,
        )
        results.append(sym)

    def _parse_tearsheet_as_search_result(
        self, url: str, tree: HtmlElement, query: str
    ) -> list[Symbol]:
        """
        Parses a single tearsheet page as a search result (happens on exact match redirect).
        """
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        symbol_code = qs.get("s", [None])[0]

        if not symbol_code:
            return []

        name_el = tree.xpath('//h1[@class="mod-tearsheet-overview__header__name"]')
        name = name_el[0].text.strip() if name_el else query

        isin = self._extract_isin_from_tearsheet(tree)
        # Validate strict Isin if extracted
        isin_val = Isin(root=isin).root if isin else (query if self._is_isin(query) else None)

        asset_class = None
        if "/etfs/" in url:
            asset_class = "ETF"
        elif "/equities/" in url:
            asset_class = "Equity"
        elif "/funds/" in url:
            asset_class = "Fund"
        elif "/indices/" in url:
            asset_class = "Index"

        return [Symbol(ticker=symbol_code, name=name, isin=isin_val, asset_class=asset_class)]

    def get_history(self, ticker: Ticker | str, days: int = 30) -> History:
        """
        Fetch historical data using the strict Chart API schemas.
        """
        ticker_val = Ticker(root=ticker) if isinstance(ticker, str) else ticker
        xid = self.get_xid(ticker_val)

        # Clean xid (remove quotes if present)
        # xid is a strictly typed Xid, convert to string for manipulation if needed,
        # but Xid.root is string.
        xid_val = xid.root.strip('"').strip("'")

        # Map days to period/interval if needed, but API accepts raw days
        # We use a standard configuration
        request_model = ChartRequest(
            days=days,
            dataPeriod=DataPeriod.DAY,
            elements=[
                ChartRequestElement(Type=ChartElementType.PRICE, Symbol=Xid(root=xid_val)),
                ChartRequestElement(Type=ChartElementType.VOLUME, Symbol=Xid(root=xid_val)),
            ],
        )

        resp = self.client.post(
            "/data/chartapi/series", json=request_model.model_dump(by_alias=True)
        )
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 400:
                logger.debug("HTTP 400 Error for /data/chartapi/series: %s", resp.text)
            raise e

        # Parse with Pydantic
        chart_data = ChartResponse(**resp.json())

        return self._convert_to_history(ticker_val, chart_data)

    def get_xid(self, ticker: Ticker) -> Xid:
        """
        Extract internal XID for a ticker.
        """
        url_summary = f"/data/equities/tearsheet/summary?s={ticker.root}"
        # Note: Valid for Equities/ETFs/Indices usually, if not we might need adaptive URLs
        # But commonly ?s=TICKER works for lookup or redirects
        resp = self.client.get(url_summary)
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 400:
                logger.debug("HTTP 400 Error for %s: %s", url_summary, resp.text)
            raise e

        tree = html.fromstring(resp.content)

        xid_str = None

        # Method A: data-mod-config
        divs = cast(list[Any], tree.xpath("//div[@data-mod-config] | //section[@data-mod-config]"))
        for div in divs:
            try:
                div_el = cast(HtmlElement, div)
                raw_cfg = div_el.get("data-mod-config")
                if raw_cfg:
                    # It might be URL-encoded or HTML-escaped
                    decoded_cfg = html_lib.unescape(raw_cfg)
                    cfg = json.loads(decoded_cfg)
                    if "xid" in cfg:
                        xid_str = str(cfg["xid"])
                        break
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                logger.debug("Failed to parse data-mod-config: %s", e)
                continue

        if not xid_str:
            # Method B: Regex fallback
            regex = r'(?:xid|&quot;xid&quot;)\s*[:=]\s*(?:["\']|&quot;)?(\d+)(?:["\']|&quot;)?'
            match = re.search(regex, resp.text)
            if match:
                xid_str = match.group(1)

        if not xid_str:
            raise ScraperError(f"Could not determine internal FT ID for ticker {ticker.root}")

        return Xid(root=xid_str)

    def _convert_to_history(self, ticker: Ticker, data: ChartResponse) -> History:
        """
        Convert strictly typed API response to pydantic-market-data History.
        """
        ranges = []

        # Find Price and Volume elements
        price_el = next((e for e in data.elements if e.type == ChartElementType.PRICE), None)
        vol_el = next((e for e in data.elements if e.type == ChartElementType.VOLUME), None)

        if not price_el:
            return History(symbol=Symbol(ticker=ticker, name=ticker.root), candles=[])

        # Extract series
        # Helper to get values list safely
        def get_values(series_list: list[ComponentSeries], type_name: str) -> list[float]:
            found = next((s for s in series_list if s.type == type_name), None)
            return found.values if found else []

        highs = get_values(price_el.component_series, "High")
        lows = get_values(price_el.component_series, "Low")
        opens = get_values(price_el.component_series, "Open")
        closes = get_values(price_el.component_series, "Close")
        vols = get_values(vol_el.component_series, "Volume") if vol_el else []

        for i, dt in enumerate(data.dates):
            c_open = opens[i] if i < len(opens) else None
            c_high = highs[i] if i < len(highs) else None
            c_low = lows[i] if i < len(lows) else None
            c_close = closes[i] if i < len(closes) else None
            c_vol = vols[i] if i < len(vols) else None

            # Pydantic-market-data expects datetime (naive or aware)
            # data.dates are strictly typed datetime from CheckRequest
            ranges.append(
                OHLCV(
                    date=dt,
                    open=c_open,
                    high=c_high,
                    low=c_low,
                    close=c_close,
                    volume=c_vol,
                )
            )

        return History(symbol=Symbol(ticker=ticker, name=ticker.root), candles=ranges)

    # --- Helpers ---

    def _extract_isin_from_tearsheet(self, tree: HtmlElement) -> str | None:
        isin_els = tree.xpath("//th[text()='ISIN']/following-sibling::td")
        if isin_els and isin_els[0].text:
            return isin_els[0].text.strip()
        return None

    def _map_country_to_code(self, country_name: str | None) -> str | None:
        if not country_name:
            return None
        mapping = {
            "United Kingdom": "GB",
            "United States": "US",
            "France": "FR",
            "Germany": "DE",
            "Canada": "CA",
            "Italy": "IT",
            "Spain": "ES",
            "Netherlands": "NL",
            "Australia": "AU",
            "Japan": "JP",
            "Switzerland": "CH",
            "Sweden": "SE",
            "Belgium": "BE",
            "Ireland": "IE",
            "Denmark": "DK",
            "Finland": "FI",
            "Norway": "NO",
            "Portugal": "PT",
            "Hong Kong": "HK",
            "Singapore": "SG",
            "China": "CN",
            "India": "IN",
        }
        return mapping.get(country_name, None)

    def _extract_currency(self, ticker: str) -> Currency | None:
        parts = ticker.split(":")
        if len(parts) >= 2:
            # Check last or second to last part for currency
            # Currencies are usually 3 letters
            known_currencies = {
                "USD",
                "EUR",
                "GBP",
                "JPY",
                "CHF",
                "CAD",
                "AUD",
                "HKD",
                "SGD",
                "SEK",
                "NOK",
                "DKK",
                "MXN",
                "BRL",
                "ZAR",
                "INR",
                "CNY",
                "KRW",
            }
            for p in reversed(parts):
                p_up = p.upper()
                if p_up == "GBX":
                    return cast(Currency, "GBP")
                if p_up in known_currencies:
                    return cast(Currency, p_up)

            # Heuristic: if 3 parts and last is 3 letters, assume currency if not known exchange
            if len(parts) >= 3:
                last = parts[-1].upper()
                if len(last) == 3 and last.isalpha():
                    # Avoid common exchange codes
                    if last not in {
                        "FRA",
                        "HAN",
                        "GER",
                        "NSQ",
                        "PAR",
                        "MIL",
                        "MAD",
                        "LIS",
                        "LON",
                    }:
                        return cast(Currency, last)
        return None

    def _map_country_to_currency(self, country_code: str | None) -> Currency | None:
        if not country_code:
            return None
        mapping = {
            "US": "USD",
            "GB": "GBP",
            "FR": "EUR",
            "DE": "EUR",
            "IT": "EUR",
            "ES": "EUR",
            "NL": "EUR",
            "BE": "EUR",
            "IE": "EUR",
            "PT": "EUR",
            "FI": "EUR",
            "CA": "CAD",
            "AU": "AUD",
            "JP": "JPY",
            "CH": "CHF",
            "SE": "SEK",
            "NO": "NOK",
            "DK": "DKK",
            "HK": "HKD",
            "SG": "SGD",
            "CN": "CNY",
            "IN": "INR",
        }
        curr = mapping.get(country_code)
        return cast(Currency, curr) if curr else None

    def _is_isin(self, query: str) -> bool:
        return len(query) == 12 and query[:2].isalpha() and query[2:].isalnum()


# Singleton instance not strictly needed but useful for API
scraper = Scraper()
