import html as html_lib
import json
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import pandas as pd
from lxml import html
from pydantic_market_data.interfaces import DataSource
from pydantic_market_data.models import OHLCV, History, SecurityCriteria, Symbol

from .client import client


def search(query: str) -> list[Symbol]:
    """
    Search for a security by ISIN, symbol, or name.
    """
    url = "/data/search"
    response = client.get(url, params={"query": query})
    response.raise_for_status()

    tree = html.fromstring(response.content)

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
    }

    results = []

    # The search results are grouped by asset class in tab panels or sections
    xpath_query = '//div[@role="tabpanel"] | //div[contains(@class, "mod-search-results__section")]'
    panels = tree.xpath(xpath_query)
    if not panels:
        # Fallback for direct redirect or simple structure
        if "tearsheet" in response.url:
            # ... existing redirect logic ...
            parsed = urlparse(response.url)
            qs = parse_qs(parsed.query)
            symbol_code = qs.get("s", [None])[0]
            if symbol_code:
                name_el = tree.xpath('//h1[@class="mod-tearsheet-overview__header__name"]')
                name = name_el[0].text.strip() if name_el else query
                return [Symbol(ticker=symbol_code, name=name)]
        panels = [tree]

    for panel in panels:
        # Identify asset class from ID or header
        panel_id = panel.get("id")
        asset_type = asset_class_map.get(panel_id)

        if not asset_type:
            header = panel.xpath(".//h3")
            ft_name = header[0].text.strip() if header else None
            asset_type = asset_class_map.get(ft_name, ft_name)

        rows = panel.xpath('.//table[contains(@class, "mod-ui-table")]/tbody/tr')
        for row in rows:
            cols = row.xpath("./td")
            if len(cols) >= 2:
                name = cols[0].text_content().strip()
                ticker = cols[1].text_content().strip()
                exchange = cols[2].text_content().strip() if len(cols) > 2 else None
                country = cols[3].text_content().strip() if len(cols) > 3 else None

                if ticker:
                    country_code = _map_country_to_code(country)

                    # Currency extraction from ticker: "4GLD:GER:EUR" -> "EUR"
                    # Note: FT tickers are variable format, often SYMBOL:EXCHANGE:CURRENCY
                    currency = None
                    ticker_parts = ticker.split(":")
                    if len(ticker_parts) >= 3:
                        # If there are 3 parts, the last one is likely the currency
                        last_part = ticker_parts[-1].upper()
                        if len(last_part) == 3 and last_part.isalpha():
                            currency = last_part

                    try:
                        sym = Symbol(
                            ticker=ticker,
                            name=name,
                            exchange=exchange,
                            country=country_code,
                            currency=currency,
                            asset_class=asset_type,
                        )
                    except Exception:
                        # If validation fails, try a more lenient approach
                        sym = Symbol(
                            ticker=ticker,
                            name=name,
                            exchange=exchange,
                            country=country_code,
                            currency=None,
                            asset_class=asset_type,
                        )

                    results.append(sym)

    return results


def _map_country_to_code(country_name: str | None) -> str | None:
    if not country_name:
        return None

    # Common mappings
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

    # Return None if not mapped to avoid validation error for unknown names
    return mapping.get(country_name, None)


def resolve_ticker(
    isin: str | None = None,
    symbol: str | None = None,
    description: str | None = None,
    preferred_exchanges: list[str] | None = None,
    target_price: float | None = None,
    target_date: str | None = None,
    currency: str | None = None,
    country: str | None = None,
    asset_class: str | None = None,
    limit: int = 1,
) -> list[Symbol]:
    """
    Enhanced resolve_ticker that returns a list of matching Symbols.
    """
    candidates = []

    # 1. Search Strategy
    if isin:
        candidates = search(isin)
    if not candidates and symbol:
        candidates = search(symbol)
    if not candidates and description:
        candidates = search(description)

    if not candidates:
        return []

    # 2. Filtering
    filtered = []
    for cand in candidates:
        # Currency Filter
        if currency:
            if not cand.currency or str(cand.currency).upper() != currency.upper():
                continue

        # Country Filter
        if country:
            if not cand.country or str(cand.country).upper() != country.upper():
                continue

        # Asset Class Filter
        if asset_class:
            cand_asset = getattr(cand, "asset_class", None)
            if not cand_asset or cand_asset.upper() != asset_class.upper():
                continue

        filtered.append(cand)

    if not filtered:
        return []

    # 3. Sorting (Preferences) - Moved before validation for early exit optimization
    def match_score(cand):
        score = 0
        # Factor A: Exchange Priority
        if preferred_exchanges:
            prefs_lower = [p.lower() for p in preferred_exchanges]
            cand_exc = (cand.exchange or "").lower()
            cand_sym = cand.ticker.lower()
            found_exc = False
            for i, p in enumerate(prefs_lower):
                if p in cand_exc or p in cand_sym:
                    score += i
                    found_exc = True
                    break
            if not found_exc:
                score += len(prefs_lower)

        # Factor B: Currency Match (Highest priority after exchange if not already filtered)
        if currency:
            if cand.ticker.upper().endswith(f":{currency.upper()}"):
                score -= 100

        return score

    filtered.sort(key=match_score)

    # 4. Validation (Price)
    # If price and date are provided, we only keep candidates that traded near that price.
    if target_price and target_date:
        validated = []

        # Determine necessary history period
        val_period = "3mo"
        try:
            target_dt = None
            for fmt in ("%Y-%m-%d", "%Y%m%d"):
                try:
                    target_dt = datetime.strptime(target_date, fmt)
                    break
                except ValueError:
                    pass
            if target_dt:
                days_diff = (datetime.now() - target_dt).days
                if days_diff > 365 * 5:
                    val_period = "10y"
                elif days_diff > 365 * 2:
                    val_period = "5y"
                elif days_diff > 365:
                    val_period = "3y"
                elif days_diff > 180:
                    val_period = "1y"
                elif days_diff > 90:
                    val_period = "6mo"
        except Exception:
            pass

        for cand in filtered:
            try:
                hist = history(cand.ticker, period=val_period)
                dt = target_dt
                if not dt:
                    for fmt in ("%Y-%m-%d", "%Y%m%d"):
                        try:
                            dt = datetime.strptime(target_date, fmt)
                            break
                        except ValueError:
                            pass

                if not dt:
                    continue

                match = _check_price_match_logic(hist, dt, target_price)
                if match:
                    validated.append(cand)
                    # Early Exit: If we reached the limit, we can stop
                    if limit > 0 and len(validated) >= limit:
                        break
            except Exception:
                continue
        filtered = validated

    if not filtered:
        return []

    # 5. Limit
    if limit > 0:
        return filtered[:limit]

    return filtered


def history(ticker: str, period: str = "1mo") -> History:
    """
    Fetch historical data for a ticker.
    Period options: '1d', '1mo', '3mo', '6mo', '1y', '3y', '5y', '10y', 'max'
    """
    # 1. Get Summary to find Internal ID (xid)
    # Note: Optimization - we could cache this mapping
    url_summary = f"/data/equities/tearsheet/summary?s={ticker}"
    resp = client.get(url_summary)
    resp.raise_for_status()

    tree = html.fromstring(resp.content)

    # Extract xid
    xid = None
    # Method A: data-mod-config
    divs = tree.xpath("//div[@data-mod-config]")
    for div in divs:
        try:
            raw_cfg = div.get("data-mod-config")
            if raw_cfg:
                decoded_cfg = html_lib.unescape(raw_cfg)
                cfg = json.loads(decoded_cfg)
                if "xid" in cfg:
                    xid = cfg["xid"]
                    break
        except (ValueError, KeyError, json.JSONDecodeError):
            pass

    if not xid:
        # Method B: Regex (Handle potentially escaped quotes)
        # Matches: xid:"123" or xid="123" or "xid":"123"
        # Also handle &quot;
        # Look for xid followed by colon/equals, optional quotes, and digits
        regex = r'(?:xid|&quot;xid&quot;)\s*[:=]\s*(?:["\']|&quot;)?(\d+)(?:["\']|&quot;)?'
        match = re.search(regex, resp.text)
        if match:
            xid = match.group(1)

    if not xid:
        raise ValueError(f"Could not determine internal FT ID for ticker {ticker}")

    # 2. Fetch Series Data
    # Map period to days
    days_map = {
        "1d": 2,  # safety
        "1mo": 35,
        "3mo": 100,
        "6mo": 200,
        "1y": 365,
        "3y": 365 * 3,
        "5y": 365 * 5,
        "10y": 365 * 10,
        "max": 365 * 30,
    }
    days = days_map.get(period, 365)

    payload = {
        "days": days,
        "dataNormalized": False,
        "dataPeriod": "Day",
        "dataInterval": 1,
        "realtime": False,
        "yFormat": "0.###",
        "timeServiceFormat": "JSON",
        "returnDateType": "ISO8601",
        "elements": [{"Type": "price", "Symbol": xid}, {"Type": "volume", "Symbol": xid}],
    }

    resp_series = client.post("/data/chartapi/series", json=payload)
    resp_series.raise_for_status()

    data = resp_series.json()

    # 3. Parse Response
    candles = []
    dates = data.get("Dates", [])
    elements = data.get("Elements", [])

    if not dates or not elements:
        return History(symbol=Symbol(ticker=ticker, name=ticker), candles=[])

    # Find Price and Volume components
    price_comp = next((e for e in elements if e.get("Type") == "price"), None)
    vol_comp = next((e for e in elements if e.get("Type") == "volume"), None)

    if not price_comp:
        return History(symbol=Symbol(ticker=ticker, name=ticker), candles=[])

    ohlc = price_comp.get("ComponentSeries", [])
    volumes = vol_comp.get("ComponentSeries", []) if vol_comp else []

    # Let's extract vectors first
    highs = next((s["Values"] for s in ohlc if s["Type"] == "High"), [])
    lows = next((s["Values"] for s in ohlc if s["Type"] == "Low"), [])
    opens = next((s["Values"] for s in ohlc if s["Type"] == "Open"), [])
    closes = next((s["Values"] for s in ohlc if s["Type"] == "Close"), [])
    vols = next((s["Values"] for s in volumes if s["Type"] == "Volume"), [])

    for i, d_str in enumerate(dates):
        dt = datetime.fromisoformat(d_str)
        c_open = opens[i] if i < len(opens) else None
        c_high = highs[i] if i < len(highs) else None
        c_low = lows[i] if i < len(lows) else None
        c_close = closes[i] if i < len(closes) else None
        c_vol = vols[i] if i < len(vols) else None

        candles.append(
            OHLCV(date=dt, open=c_open, high=c_high, low=c_low, close=c_close, volume=c_vol)
        )

    # Try to extract real name from earlier response?
    # We don't have it easily here without another request or parsing summary more.
    # Ticker defaults to just ticker string for now.
    sym = Symbol(ticker=ticker, name=ticker)

    return History(symbol=sym, candles=candles)


def _check_price_match_logic(history: History, target_dt: datetime, target_price: float) -> bool:
    """Internal helper for validation logic"""
    # Convert to DataFrame for easier lookups or iterate
    # Let's iterate efficiently
    # Find exact date
    match_row = None
    target_ts = pd.Timestamp(target_dt)

    # Check exact date (ignoring time)
    for c in history.candles:
        if pd.Timestamp(c.date).date() == target_ts.date():
            match_row = c
            break

    # Fallback: Nearest (within 3 days)
    if not match_row:
        # Sort by diff
        candidates = []
        for c in history.candles:
            diff = abs((pd.Timestamp(c.date).date() - target_ts.date()).days)
            if diff <= 3:
                candidates.append((diff, c))

        if candidates:
            # Sort by diff
            candidates.sort(key=lambda x: x[0])
            match_row = candidates[0][1]

    if not match_row:
        return False

    # Range Check
    if match_row.high is not None and match_row.low is not None:
        if match_row.low <= target_price <= match_row.high:
            return True
        else:
            return False  # Strict range check

    # Close Check
    if match_row.close is not None:
        diff = abs(match_row.close - target_price)
        pct = diff / target_price
        return pct < 0.05

    return False


class FTDataSource(DataSource):
    """
    Financial Times (markets.ft.com) data source implementation.
    """

    def search(self, query: str) -> list[Symbol]:
        return search(query)

    def resolve(self, criteria: SecurityCriteria) -> Symbol | None:
        # Note: SecurityCriteria in pydantic-market-data might not
        # have preferred_exchanges yet. We use what's available.
        results = resolve_ticker(
            isin=criteria.isin,
            symbol=criteria.symbol,
            description=criteria.description,
            preferred_exchanges=None,
            target_price=criteria.target_price,
            target_date=str(criteria.target_date) if criteria.target_date else None,
            currency=criteria.currency,
            limit=1,
        )
        if results:
            return results[0]
        return None

    def history(self, ticker: str, period: str = "1mo") -> History:
        return history(ticker, period=period)

    def validate(self, ticker: str, target_date: Any, target_price: float) -> bool:
        """
        Validates if the ticker traded near the target price on the target date.
        """
        try:
            hist = self.history(ticker, period="3mo")
            # Convert target_date to datetime if it's a date or str
            if isinstance(target_date, str):
                dt = None
                for fmt in ("%Y-%m-%d", "%Y%m%d"):
                    try:
                        dt = datetime.strptime(target_date, fmt)
                        break
                    except ValueError:
                        pass
            elif isinstance(target_date, date):
                dt = datetime.combine(target_date, datetime.min.time())
            else:
                dt = target_date

            if not dt:
                return False

            return _check_price_match_logic(hist, dt, target_price)
        except Exception:
            return False
