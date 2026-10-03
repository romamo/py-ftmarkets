"""Quote currency of FT lines, pence as GBX, through an injected fake FTClient (#8).

The pages are trimmed from live markets.ft.com: the results page names no currency unless
the symbol carries it (``VUSA:LSE:GBP``, ``3LVO:LSE:GBX``), and the tearsheet states it in
the quote module's ``data-mod-config`` and the "Price (GBX)" label.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from pydantic_market_data.models import SecurityQuery

from ftmarkets.api import FTDataSource
from ftmarkets.extract.scraper import Scraper, ScraperError

SEARCH_URL = "https://markets.ft.com/data/search?query=x"
TEARSHEET_URL = "https://markets.ft.com/data/equities/tearsheet/summary?s="


def search_row(name: str, symbol: str, exchange: str, country: str) -> str:
    return (
        f'<tr><td><a href="/data/equities/tearsheet/summary?s={symbol}">{name}</a></td>'
        f"<td>{symbol}</td><td>{exchange}</td><td>{country}</td></tr>"
    )


def search_page(*rows: str, panel: str = "equity-panel") -> str:
    return (
        f'<html><div id="{panel}" role="tabpanel"><table class="mod-ui-table"><tbody>'
        + "".join(rows)
        + "</tbody></table></div></html>"
    )


VODAFONE = search_page(
    search_row("Vodafone Group PLC", "VOD:LSE", "London Stock Exchange", "United Kingdom"),
    search_row("Vodafone Group PLC", "VOD:NSQ", "NASDAQ", "United States"),
    search_row("Vanguard S&amp;P 500", "VUSA:LSE:GBP", "London Stock Exchange", "United Kingdom"),
    search_row("Vanguard S&amp;P 500", "VUSD:LSE:USD", "London Stock Exchange", "United Kingdom"),
    search_row("GraniteShares 3x Long", "3LVO:LSE:GBX", "London Stock Exchange", "United Kingdom"),
)


def quote_config(currency: str | None) -> str:
    stated = f"&quot;currency&quot;:&quot;{currency}&quot;," if currency else ""
    return (
        '<div data-mod-config="{&quot;xid&quot;:&quot;281891&quot;,'
        f'&quot;assetClass&quot;:&quot;Equity&quot;,{stated}&quot;symbol&quot;:&quot;X&quot;}}">'
        "</div>"
    )


def price_label(currency: str) -> str:
    return (
        '<ul><li><span class="mod-ui-data-list__label">'
        f'Price ({currency})</span><span class="mod-ui-data-list__value">126.80</span></li></ul>'
    )


SYMBOL_CHAIN = """
<div class="mod-tearsheet-overview__header__symbol"><div class="mod-ui-symbol-chain">
<span class="mod-ui-symbol-chain__trigger">VOD:LSE</span>
<div class="mod-ui-overlay"><ul class="mod-ui-action-menu__menu--list">
<li class="mod-ui-symbol-chain__country"><i class="mod-sprite-flags--gb"></i>United Kingdom</li>
<li><a href="/data/equities/tearsheet/summary?s=VOD:LSE" class="mod-ui-link">
  <span>VOD:LSE</span><span>London Stock Exchange</span></a></li>
<li class="mod-ui-symbol-chain__country"><i class="mod-sprite-flags--us"></i>United States</li>
<li><a href="/data/equities/tearsheet/summary?s=VOD:NSQ" class="mod-ui-link">
  <span>VOD:NSQ</span><span>NASDAQ</span></a></li>
</ul></div></div></div>
"""


def tearsheet(*parts: str, name: str = "Vodafone Group PLC") -> str:
    header = f'<h1 class="mod-tearsheet-overview__header__name">{name}</h1>'
    return "<html>" + header + "".join(parts) + "</html>"


VOD_TEARSHEET = tearsheet(SYMBOL_CHAIN, quote_config("GBX"), price_label("GBX"))

CHART = {
    "Dates": ["2026-10-01T00:00:00"],
    "Elements": [
        {
            "Type": "price",
            "Symbol": "281891",
            "ComponentSeries": [
                {"Type": "Open", "Values": [124.5]},
                {"Type": "High", "Values": [128.05]},
                {"Type": "Low", "Values": [124.15]},
                {"Type": "Close", "Values": [126.8]},
            ],
        }
    ],
}


@dataclass
class FakeResponse:
    content: bytes
    url: str
    payload: dict[str, Any] | None = None
    status_code: int = 200

    @property
    def text(self) -> str:
        return self.content.decode()

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        if self.payload is None:
            raise AssertionError("this fake response has no JSON")
        return self.payload


@dataclass
class RoutingClient:
    """A fake FTClient: ``/data/search`` answers ``search`` from ``search_url`` (a tearsheet
    URL for FT's exact-match redirect), a tearsheet GET answers ``tearsheets[symbol]``, and
    the chart API answers ``CHART``. Records every GET."""

    search: str
    tearsheets: dict[str, str] = field(default_factory=dict)
    search_url: str = SEARCH_URL
    calls: list[tuple[str, dict[str, Any] | None]] = field(default_factory=list)

    def get(self, path: str, params: dict[str, Any] | None = None) -> FakeResponse:
        self.calls.append((path, params))
        if path == "/data/search":
            return FakeResponse(content=self.search.encode(), url=self.search_url)
        if path == "/data/equities/tearsheet/summary" and params is not None:
            page = self.tearsheets[params["s"]]
            return FakeResponse(content=page.encode(), url=TEARSHEET_URL + params["s"])
        raise AssertionError(f"unexpected GET {path} {params}")

    def post(self, path: str, json: dict[str, Any] | None = None) -> FakeResponse:
        if path != "/data/chartapi/series":
            raise AssertionError(f"unexpected POST {path}")
        return FakeResponse(content=b"", url="", payload=CHART)

    def tearsheet_gets(self) -> list[str]:
        return [p["s"] for path, p in self.calls if path != "/data/search" and p is not None]


def scraper(client: RoutingClient) -> Scraper:
    return Scraper(http_client=client)  # type: ignore[arg-type]


def currencies(client: RoutingClient) -> dict[str, str | None]:
    return {
        str(s.symbol): (str(s.currency) if s.currency else None)
        for s in scraper(client).search("VOD")
    }


def test_search_labels_pence_lines_gbx_from_their_tearsheet():
    client = RoutingClient(VODAFONE, tearsheets={"VOD:LSE": VOD_TEARSHEET})
    assert currencies(client) == {
        "VOD:LSE": "GBX",
        "VOD:NSQ": "USD",
        "VUSA:LSE:GBP": "GBP",
        "VUSD:LSE:USD": "USD",
        "3LVO:LSE:GBX": "GBX",
    }
    # Only the UK line whose symbol names no currency costs a tearsheet fetch
    assert client.tearsheet_gets() == ["VOD:LSE"]


def test_search_keeps_gbp_where_the_tearsheet_says_gbp():
    page = tearsheet(quote_config("GBP"), price_label("GBP"))
    client = RoutingClient(VODAFONE, tearsheets={"VOD:LSE": page})
    assert currencies(client)["VOD:LSE"] == "GBP"


def test_search_leaves_uk_line_unlabelled_when_its_tearsheet_states_no_currency():
    client = RoutingClient(VODAFONE, tearsheets={"VOD:LSE": tearsheet(quote_config(None))})
    assert currencies(client)["VOD:LSE"] is None


def test_search_reads_the_price_label_when_the_config_has_no_currency():
    page = tearsheet(quote_config(None), price_label("GBX"))
    client = RoutingClient(VODAFONE, tearsheets={"VOD:LSE": page})
    assert currencies(client)["VOD:LSE"] == "GBX"


def test_search_fails_on_a_tearsheet_stating_an_unknown_currency():
    client = RoutingClient(VODAFONE, tearsheets={"VOD:LSE": tearsheet(quote_config("XYZ"))})
    with pytest.raises(ScraperError, match="unknown currency: 'XYZ'"):
        scraper(client).search("VOD")


def test_search_fails_on_a_currency_config_that_is_not_json():
    broken = '<div data-mod-config="{currency: GBX"></div>'
    client = RoutingClient(VODAFONE, tearsheets={"VOD:LSE": tearsheet(broken)})
    with pytest.raises(ScraperError, match="not JSON"):
        scraper(client).search("VOD")


def test_search_skips_unrelated_and_list_configs_when_reading_the_currency():
    # An ETF's holdings list mentions currency but is no quote config; an unrelated
    # module's config that never mentions currency is not even parsed
    holdings = '<div data-mod-config="[{&quot;currency&quot;:&quot;USD&quot;}]"></div>'
    unrelated = '<div data-mod-config="not json at all"></div>'
    page = tearsheet(unrelated, holdings, quote_config("GBX"))
    client = RoutingClient(VODAFONE, tearsheets={"VOD:LSE": page})
    assert currencies(client)["VOD:LSE"] == "GBX"


def test_search_uk_index_keeps_country_currency_without_a_fetch():
    page = search_page(
        search_row("FTSE 100 Index", "FTSE:FSI", "FTSE International", "United Kingdom"),
        panel="index-panel",
    )
    client = RoutingClient(page)
    (index,) = scraper(client).search("FTSE")
    assert str(index.currency) == "GBP"
    assert client.tearsheet_gets() == []


def test_exact_match_tearsheet_states_currency_exchange_and_country():
    client = RoutingClient(VOD_TEARSHEET, search_url=TEARSHEET_URL + "VOD:LSE")
    (vod,) = scraper(client).search("VOD:LSE")
    assert (str(vod.currency), vod.exchange, str(vod.country)) == (
        "GBX",
        "London Stock Exchange",
        "GB",
    )


def test_exact_match_tearsheet_takes_currency_from_symbol_suffix():
    page = tearsheet(name="Apple Inc")
    client = RoutingClient(page, search_url=TEARSHEET_URL + "AAPL:NSQ:USD")
    (apple,) = scraper(client).search("AAPL:NSQ:USD")
    assert (str(apple.currency), apple.exchange, apple.country) == ("USD", None, None)


def test_resolve_with_currency_finds_exact_match_tearsheet():
    page = tearsheet(name="Apple Inc")
    client = RoutingClient(page, search_url=TEARSHEET_URL + "AAPL:NSQ:USD")
    found = FTDataSource(scraper(client)).resolve(
        SecurityQuery(symbol="AAPL:NSQ:USD", currency="USD")
    )
    assert found is not None and str(found.symbol) == "AAPL:NSQ:USD"


@pytest.mark.parametrize(("currency", "expected"), [("GBX", "VOD:LSE"), ("GBp", "VOD:LSE")])
def test_resolve_filters_pence_lines_as_gbx(currency: str, expected: str):
    client = RoutingClient(VODAFONE, tearsheets={"VOD:LSE": VOD_TEARSHEET})
    found = FTDataSource(scraper(client)).resolve(SecurityQuery(symbol="VOD", currency=currency))
    assert found is not None and str(found.symbol) == expected


def test_resolve_gbp_no_longer_matches_pence_lines():
    client = RoutingClient(VODAFONE, tearsheets={"VOD:LSE": VOD_TEARSHEET})
    found = FTDataSource(scraper(client)).resolve(SecurityQuery(symbol="VOD", currency="GBP"))
    assert found is not None and str(found.symbol) == "VUSA:LSE:GBP"


def test_history_carries_the_stated_currency_and_ft_numbers():
    client = RoutingClient("", tearsheets={"VOD:LSE": VOD_TEARSHEET})
    history = scraper(client).get_history("VOD:LSE", days=5)
    assert str(history.security.currency) == "GBX"
    assert [(c.date.date(), c.close) for c in history.candles] == [(date(2026, 10, 1), 126.8)]
    # The XID and the currency come from one tearsheet fetch
    assert client.tearsheet_gets() == ["VOD:LSE"]
