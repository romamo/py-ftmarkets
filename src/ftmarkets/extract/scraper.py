import html as html_lib
import json
import logging
import re
from typing import cast
from urllib.parse import parse_qs, urlparse

import requests
from lxml import html
from lxml.html import HtmlElement
from pydantic import ValidationError
from pydantic_extra_types.country import CountryAlpha2
from pydantic_extra_types.currency_code import Currency
from pydantic_market_data.models import ISIN, OHLCV, AssetClass, History, Security

from ..client import FTClient, client
from .schemas import (
    ChartElementType,
    ChartRequest,
    ChartRequestElement,
    ChartResponse,
    ComponentSeries,
    ComponentSeriesType,
    DataPeriod,
    Symbol,
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

    def search(self, query: Symbol.Input) -> list[Security]:
        """
        Search for a security by ISIN, symbol, or name.
        Parsing logic is strict but resilient to HTML changes where possible.
        """
        query_str = str(query.root if hasattr(query, "root") else query)
        url = "/data/search"
        response = self.client.get(url, params={"query": query_str})
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if response.status_code == 400:
                logger.debug("HTTP 400 Error for %s: %s", url, response.text)
            raise e

        tree = cast(HtmlElement, html.fromstring(response.content))

        # Check for direct redirect (tearsheet); only the path counts, since the query
        # string carries the user's search text
        if "/tearsheet/" in urlparse(response.url).path:
            return self._parse_tearsheet_as_search_result(response.url, tree, query_str)

        # Standard search results page
        return self._parse_search_results(tree, self._parse_isin(query_str))

    def _parse_search_results(self, tree: HtmlElement, query_isin: ISIN | None) -> list[Security]:
        """
        `query_isin` is the query when it is a valid ISIN: FT's results table for an
        ISIN query lists that security's listings, so table rows carry it. Other
        tearsheet links on the page (best matches) may be unrelated and do not.
        """
        results: list[Security] = []
        # Mapping for FT tab IDs/names to (AssetClass, security_type)
        asset_class_map: dict[str, tuple[AssetClass | None, str | None]] = {
            "etf-panel": (AssetClass.EQUITY, "ETF"),
            "equity-panel": (AssetClass.EQUITY, "Equity"),
            "fund-panel": (None, "Fund"),
            "index-panel": (AssetClass.INDEX, "Index"),
            "ETFs": (AssetClass.EQUITY, "ETF"),
            "Equities": (AssetClass.EQUITY, "Equity"),
            "Funds": (None, "Fund"),
            "Indices": (AssetClass.INDEX, "Index"),
            "Indicies": (AssetClass.INDEX, "Index"),
            "etfs": (AssetClass.EQUITY, "ETF"),
            "equities": (AssetClass.EQUITY, "Equity"),
            "funds": (None, "Fund"),
            "indices": (AssetClass.INDEX, "Index"),
        }

        xpath_query = (
            '//div[@role="tabpanel"] | //div[contains(@class, "mod-search-results__section")]'
        )
        panels = tree.xpath(xpath_query)

        # 1. Standard Panel Results
        for panel in panels:
            panel_id = panel.get("id")
            ac_pair: tuple[AssetClass | None, str | None] = asset_class_map.get(
                panel_id, (None, None)
            )
            if ac_pair == (None, None):
                header = panel.xpath(".//h3")
                if header:
                    ft_name = header[0].text_content().strip()
                    ac_pair = asset_class_map.get(ft_name, (None, ft_name))
            asset_class, security_type = ac_pair

            rows = panel.xpath('.//table[contains(@class, "mod-ui-table")]/tbody/tr')
            for row in rows:
                cols = row.xpath("./td")
                if len(cols) >= 2:
                    name = cols[0].text_content().strip()
                    symbol_str = cols[1].text_content().strip()
                    exchange = self._own_text(cols[2]) if len(cols) > 2 else None
                    country = cols[3].text_content().strip() if len(cols) > 3 else None
                    self._add_to_results(
                        results,
                        symbol_str,
                        name,
                        exchange,
                        country,
                        asset_class,
                        security_type,
                        query_isin,
                    )

        # 2. Capture ALL tearsheet links on the page (covers "Best Match" and other lists)
        # Avoid duplicates and ensure they look like symbols
        all_links = tree.xpath('//a[contains(@href, "tearsheet/summary?s=")]')
        for link in all_links:
            href = link.get("href")
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            symbol_str = qs.get("s", [None])[0]
            name = link.text_content().strip()
            if symbol_str and not any(str(r.symbol) == symbol_str for r in results):
                link_ac_pair: tuple[AssetClass | None, str | None] = (None, None)
                for at_key in ["equities", "etfs", "funds", "indices"]:
                    if f"/{at_key}/" in href:
                        link_ac_pair = asset_class_map.get(at_key, (None, at_key.capitalize()))
                        break
                link_asset_class, link_security_type = link_ac_pair
                self._add_to_results(
                    results,
                    symbol_str,
                    name,
                    None,
                    None,
                    link_asset_class,
                    link_security_type,
                    None,
                )

        return results

    def _add_to_results(
        self,
        results: list[Security],
        symbol: str,
        name: str,
        exchange: str | None,
        country: str | None,
        asset_class: AssetClass | None,
        security_type: str | None,
        isin: ISIN | None,
    ) -> None:
        country_code = self._map_country_to_code(country)
        currency = self._extract_currency(symbol) or self._map_country_to_currency(country_code)

        sec = Security(
            symbol=symbol,
            name=name,
            exchange=exchange,
            country=cast(CountryAlpha2 | None, country_code),
            currency=currency,
            asset_class=asset_class,
            security_type=security_type,
            isin=isin,
        )
        results.append(sec)

    def _parse_tearsheet_as_search_result(
        self, url: str, tree: HtmlElement, query: str
    ) -> list[Security]:
        """
        Parses a single tearsheet page as a search result (happens on exact match redirect).
        """
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        symbol_code = qs.get("s", [None])[0]

        if not symbol_code:
            return []

        name_el = tree.xpath('//h1[@class="mod-tearsheet-overview__header__name"]')
        name = name_el[0].text_content().strip() if name_el else query

        # FT redirects to the tearsheet on an exact match, so an ISIN query names this
        # security; an ISIN printed on the page takes precedence over the query
        isin_val = self._extract_isin_from_tearsheet(tree) or self._parse_isin(query)

        asset_class: AssetClass | None = None
        security_type: str | None = None
        if "/etfs/" in url:
            asset_class, security_type = AssetClass.EQUITY, "ETF"
        elif "/equities/" in url:
            asset_class, security_type = AssetClass.EQUITY, "Equity"
        elif "/funds/" in url:
            security_type = "Fund"
        elif "/indices/" in url:
            asset_class, security_type = AssetClass.INDEX, "Index"

        return [
            Security(
                symbol=symbol_code,
                name=name,
                isin=isin_val,
                asset_class=asset_class,
                security_type=security_type,
            )
        ]

    def get_history(self, symbol: Symbol.Input, days: int = 30) -> History:
        """
        Fetch historical data using the strict Chart API schemas.
        """
        symbol_val = Symbol(root=symbol) if isinstance(symbol, str) else symbol
        xid = self.get_xid(symbol_val)

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

        try:
            chart_data = ChartResponse(**resp.json())
        except (requests.exceptions.JSONDecodeError, ValidationError) as e:
            raise ScraperError(f"Malformed chart response for {symbol_val.root}: {e}") from e

        return self._convert_to_history(symbol_val, chart_data)

    def get_xid(self, symbol: Symbol) -> Xid:
        """
        Extract internal XID for a ticker.
        """
        url_summary = "/data/equities/tearsheet/summary"
        # Note: Valid for Equities/ETFs/Indices usually, if not we might need adaptive URLs
        # But commonly ?s=TICKER works for lookup or redirects
        resp = self.client.get(url_summary, params={"s": symbol.root})
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 400:
                logger.debug("HTTP 400 Error for %s: %s", url_summary, resp.text)
            raise e

        tree = html.fromstring(resp.content)

        xid_str = self._xid_from_mod_configs(tree, symbol)

        if not xid_str:
            # Method B: Regex fallback
            # xid: 123, xid=123, "xid": "123", 'xid':123, &quot;xid&quot;:&quot;123&quot;
            regex = (
                r'(?:"xid"|\'xid\'|&quot;xid&quot;|\bxid)\s*[:=]\s*'
                r'(?:["\']|&quot;)?(\d+)(?:["\']|&quot;)?'
            )
            match = re.search(regex, resp.text)
            if match:
                xid_str = match.group(1)

        if not xid_str:
            raise ScraperError(f"Could not determine internal FT ID for ticker {symbol.root}")

        return Xid(root=xid_str)

    _XID_WORD = re.compile(r"\bxid\b")

    def _xid_from_mod_configs(self, tree: HtmlElement, symbol: Symbol) -> str | None:
        """
        The XID from the page's ``data-mod-config`` attributes, or None to fall back
        to the regex.

        A tearsheet carries configs for unrelated modules too (charts keyed by
        ``issueID``, ETF holdings as a JSON list), so only a config that mentions
        ``xid`` is judged: it must be a JSON object, and its ``xid``, when present, a
        non-empty string or an integer. Such a config that is not JSON, not an object,
        or holds another ``xid`` raises ScraperError (FT changed its markup). An object
        without a top-level ``xid`` and configs that never mention it are skipped.
        """
        nodes = cast(
            list[HtmlElement], tree.xpath("//div[@data-mod-config] | //section[@data-mod-config]")
        )
        for node in nodes:
            raw_cfg = node.get("data-mod-config")
            if not raw_cfg:
                continue
            # lxml unescapes attributes once; FT may escape the JSON a second time
            decoded_cfg = html_lib.unescape(raw_cfg)
            if not self._XID_WORD.search(decoded_cfg):
                continue
            try:
                cfg = json.loads(decoded_cfg)
            except json.JSONDecodeError as e:
                raise ScraperError(
                    f"Tearsheet for {symbol.root} has a data-mod-config that is not JSON: "
                    f"{decoded_cfg[:200]!r}"
                ) from e
            if not isinstance(cfg, dict):
                raise ScraperError(
                    f"Tearsheet for {symbol.root} has a data-mod-config that is not a JSON "
                    f"object: {decoded_cfg[:200]!r}"
                )
            if "xid" not in cfg:
                continue
            xid = cfg["xid"]
            if isinstance(xid, bool) or not isinstance(xid, (str, int)) or xid == "":
                raise ScraperError(
                    f"Tearsheet for {symbol.root} has a data-mod-config with an invalid "
                    f"xid: {xid!r}"
                )
            return str(xid)
        return None

    def _convert_to_history(self, symbol: Symbol, data: ChartResponse) -> History:
        """
        Convert strictly typed API response to pydantic-market-data History.

        An empty ``Dates`` list is FT's "no data" answer and gives an empty History, as
        long as no series carries values. With dates, the price element must hold Open,
        High, Low, and Close series of exactly one value per date; the volume element
        may be missing (volume is then None), but a Volume series present must match the
        dates too. Anything else raises ScraperError.
        """
        security = Security(symbol=symbol, name=symbol.root)
        count = len(data.dates)

        price_el = next((e for e in data.elements if e.type == ChartElementType.PRICE), None)
        vol_el = next((e for e in data.elements if e.type == ChartElementType.VOLUME), None)

        def values(
            series_list: list[ComponentSeries], kind: ComponentSeriesType
        ) -> list[float] | None:
            found = next((s for s in series_list if s.type == kind), None)
            if found is None:
                return None
            if len(found.values) != count:
                raise ScraperError(
                    f"Chart response for {symbol.root} has {len(found.values)} {kind.value} "
                    f"values for {count} dates"
                )
            return found.values

        if price_el is None:
            if count:
                raise ScraperError(
                    f"Chart response for {symbol.root} has {count} dates but no price element"
                )
            if vol_el is not None:
                values(vol_el.component_series, ComponentSeriesType.VOLUME)
            return History(security=security, candles=[])

        ohlc: dict[ComponentSeriesType, list[float]] = {}
        for kind in (
            ComponentSeriesType.OPEN,
            ComponentSeriesType.HIGH,
            ComponentSeriesType.LOW,
            ComponentSeriesType.CLOSE,
        ):
            found = values(price_el.component_series, kind)
            if found is None:
                if count:
                    raise ScraperError(
                        f"Chart response for {symbol.root} has no {kind.value} series"
                    )
                found = []
            ohlc[kind] = found
        vols = values(vol_el.component_series, ComponentSeriesType.VOLUME) if vol_el else None

        try:
            candles = [
                OHLCV(
                    date=dt,
                    open=ohlc[ComponentSeriesType.OPEN][i],
                    high=ohlc[ComponentSeriesType.HIGH][i],
                    low=ohlc[ComponentSeriesType.LOW][i],
                    close=ohlc[ComponentSeriesType.CLOSE][i],
                    volume=vols[i] if vols is not None else None,
                )
                for i, dt in enumerate(data.dates)
            ]
            # History rejects duplicate, out-of-order, and mixed-timezone dates
            return History(security=security, candles=candles)
        except ValidationError as e:
            raise ScraperError(f"Malformed chart response for {symbol.root}: {e}") from e

    # --- Helpers ---

    def _extract_isin_from_tearsheet(self, tree: HtmlElement) -> ISIN | None:
        isin_els = tree.xpath("//th[text()='ISIN']/following-sibling::td")
        if not isin_els or not isin_els[0].text_content().strip():
            return None
        raw = isin_els[0].text_content().strip()
        isin = self._parse_isin(raw)
        if isin is None:
            raise ScraperError(f"Tearsheet states an invalid ISIN: {raw!r}")
        return isin

    @staticmethod
    def _own_text(cell: HtmlElement) -> str | None:
        """The cell's own text, without child elements such as FT's "Primary" badge."""
        text = " ".join(t.strip() for t in cell.xpath("./text()") if t.strip())
        return text or None

    def _map_country_to_code(self, country_name: str | None) -> str | None:
        if not country_name:
            return None
        import pycountry

        try:
            return pycountry.countries.lookup(country_name).alpha_2
        except LookupError:
            return None

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

    @staticmethod
    def _parse_isin(value: str) -> ISIN | None:
        """The value as an `ISIN` if it is one (format and checksum), else None."""
        try:
            return ISIN(value)
        except ValidationError:
            return None


# Singleton instance not strictly needed but useful for API
scraper = Scraper()
