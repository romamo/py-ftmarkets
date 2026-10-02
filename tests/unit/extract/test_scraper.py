"""Scraper against canned FT pages, served to a real FTClient through RecordingAdapter"""

import json

import pytest
import requests
from conftest import Reply
from pydantic_market_data.models import AssetClass, Security

from ftmarkets.client import FTClient
from ftmarkets.extract.schemas import Symbol
from ftmarkets.extract.scraper import Scraper, ScraperError, Xid

SEARCH = "/data/search"
TEARSHEET_PATH = "/data/equities/tearsheet/summary"
CHART = "/data/chartapi/series"
TEARSHEET = b'<html><body><div data-mod-config=\'{"xid":"123"}\'></div></body></html>'


@pytest.fixture
def client() -> FTClient:
    return FTClient()


@pytest.fixture
def scraper(client) -> Scraper:
    return Scraper(http_client=client)


def _posted(sent) -> list[dict]:
    return [json.loads(s.body) for s in sent if s.method == "POST"]


def test_get_xid_extraction(scraper, client, serve_ft):
    html_content = b"""
    <html>
        <body>
            <div data-mod-config='{"xid":"123456", "symbol":"TEST"}'></div>
        </body>
    </html>
    """
    adapter = serve_ft(client, {TEARSHEET_PATH: Reply(html_content)})

    xid = scraper.get_xid(Symbol(root="TEST:EX"))

    assert isinstance(xid, Xid)
    assert xid.root == "123456"
    assert str(xid) == "123456"
    assert [s.url for s in adapter.sent] == [
        "https://markets.ft.com/data/equities/tearsheet/summary?s=TEST%3AEX"
    ]


def test_get_xid_json_error_fallback(scraper, client, serve_ft):
    html_content = b"""
    <html>
        <body>
            <div data-mod-config='invalid_json_here'></div>
            <div data-mod-config='{"not_xid":"123456"}'></div>
            <script>var config = { xid: "987654" };</script>
        </body>
    </html>
    """
    serve_ft(client, {TEARSHEET_PATH: Reply(html_content)})
    assert scraper.get_xid(Symbol(root="TEST")).root == "987654"


def test_get_xid_regex_fallback(scraper, client, serve_ft):
    html_content = b"""
    <html>
        <script>
            var config = { xid: "987654" };
        </script>
    </html>
    """
    serve_ft(client, {TEARSHEET_PATH: Reply(html_content)})

    assert scraper.get_xid(Symbol(root="TEST:REGEX")).root == "987654"


def test_search_parsing(scraper, client, serve_ft):
    html_content = b"""
    <html>
        <div id="equity-panel" role="tabpanel">
            <table class="mod-ui-table">
                <tbody>
                    <tr>
                        <td>Apple Inc</td>
                        <td>AAPL:NSQ</td>
                        <td>Nasdaq</td>
                        <td>United States</td>
                    </tr>
                </tbody>
            </table>
        </div>
    </html>
    """
    serve_ft(client, {SEARCH: Reply(html_content)})

    results = scraper.search("AAPL")

    assert len(results) == 1
    sec = results[0]
    assert isinstance(sec, Security)
    assert str(sec.symbol) == "AAPL:NSQ"
    assert sec.name == "Apple Inc"
    assert str(sec.country) == "US"
    assert sec.asset_class == AssetClass.EQUITY
    assert sec.security_type == "Equity"


def test_get_history(scraper, client, serve_ft):
    xid_html = b"""<div data-mod-config='{"xid":"111222"}'></div>"""
    chart_json = {
        "Dates": ["2023-01-01T00:00:00"],
        "Elements": [
            {
                "Type": "price",
                "Symbol": "111222",
                "ComponentSeries": [
                    {"Type": "Open", "Values": [100.0]},
                    {"Type": "High", "Values": [110.0]},
                    {"Type": "Low", "Values": [90.0]},
                    {"Type": "Close", "Values": [105.0]},
                ],
            },
            {
                "Type": "volume",
                "Symbol": "111222",
                "ComponentSeries": [{"Type": "Volume", "Values": [5000]}],
            },
        ],
    }
    adapter = serve_ft(
        client,
        {TEARSHEET_PATH: Reply(xid_html), CHART: Reply(json.dumps(chart_json).encode())},
    )

    hist = scraper.get_history(Symbol(root="AAPL:NSQ"), days=10)

    assert len(hist.candles) == 1
    candle = hist.candles[0]
    assert candle.close == 105.0
    assert candle.volume == 5000

    # The chart request carries the XID read off the tearsheet and the requested days
    posted = _posted(adapter.sent)
    assert len(posted) == 1
    assert posted[0]["days"] == 10
    assert posted[0]["elements"][0]["Symbol"] == "111222"


TEARSHEET_PAGE = b"""
<html>
    <h1 class="mod-tearsheet-overview__header__name">Apple Inc</h1>
    <table><tr><th>ISIN</th><td>US0378331005</td></tr></table>
</html>
"""


def _redirected_to(kind: str, query: str = "?s=AAPL:NSQ") -> Reply:
    return Reply(TEARSHEET_PAGE, url=f"https://markets.ft.com/data/{kind}/tearsheet/summary{query}")


def test_search_tearsheet_redirect(scraper, client, serve_ft):
    serve_ft(client, {SEARCH: _redirected_to("equities")})

    results = scraper.search("US0378331005")

    assert len(results) == 1
    assert str(results[0].symbol) == "AAPL:NSQ"
    assert str(results[0].isin) == "US0378331005"
    assert results[0].asset_class == AssetClass.EQUITY
    assert results[0].security_type == "Equity"


@pytest.mark.parametrize(
    ("kind", "asset_class", "security_type"),
    [
        ("etfs", AssetClass.EQUITY, "ETF"),
        ("funds", None, "Fund"),
        ("indices", AssetClass.INDEX, "Index"),
    ],
)
def test_search_tearsheet_redirect_asset_classes(
    scraper, client, serve_ft, kind, asset_class, security_type
):
    serve_ft(client, {SEARCH: _redirected_to(kind)})
    sec = scraper.search("US0378331005")[0]
    assert (sec.asset_class, sec.security_type) == (asset_class, security_type)


def test_search_tearsheet_redirect_without_symbol_is_empty(scraper, client, serve_ft):
    serve_ft(client, {SEARCH: _redirected_to("equities", query="")})
    assert scraper.search("US0378331005") == []


def test_search_tearsheet_redirect_without_isin(scraper, client, serve_ft):
    page = b"""
    <html>
        <h1 class="mod-tearsheet-overview__header__name">Apple Inc</h1>
        <table><tr><th>Some Other Th</th><td>123</td></tr></table>
    </html>
    """
    url = "https://markets.ft.com/data/equities/tearsheet/summary?s=AAPL:NSQ"
    serve_ft(client, {SEARCH: Reply(page, url=url)})
    assert len(scraper.search("AAPL")) == 1


def test_get_xid_fails(scraper, client, serve_ft):
    serve_ft(client, {TEARSHEET_PATH: Reply(b"no xid here")})

    with pytest.raises(ScraperError, match="Could not determine internal FT ID"):
        scraper.get_xid(Symbol(root="UNKNOWN"))


def test_extract_currency_strict(scraper):
    curr = scraper._extract_currency("TICKER:EXCHANGE:USD")
    assert str(curr) == "USD"

    assert scraper._extract_currency("INVALID") is None


def test_search_parsing_funds_and_etfs(scraper, client, serve_ft):
    html_content = b"""
    <html>
        <div id="fund-panel" role="tabpanel">
            <table class="mod-ui-table">
                <tbody>
                    <tr>
                        <td>Test Fund</td>
                        <td>FUND:EX</td>
                    </tr>
                </tbody>
            </table>
        </div>
        <div class="mod-search-results__section">
            <h3>Indices</h3>
            <table class="mod-ui-table">
                <tbody>
                    <tr>
                        <td>Test Index</td>
                        <td>IDX:EX</td>
                    </tr>
                </tbody>
            </table>
        </div>
        <a href="/tearsheet/summary?s=TEAR:SHEET">Tear Sheet Link</a>
        <a href="/funds/tearsheet/summary?s=FUND2:EX">Fund 2 Link</a>
    </html>
    """
    serve_ft(client, {SEARCH: Reply(html_content)})
    results = scraper.search("TEST")

    assert len(results) == 4
    assert str(results[0].symbol) == "FUND:EX"
    assert (results[0].asset_class, results[0].security_type) == (None, "Fund")
    assert str(results[1].symbol) == "IDX:EX"
    assert (results[1].asset_class, results[1].security_type) == (AssetClass.INDEX, "Index")
    assert str(results[2].symbol) == "TEAR:SHEET"
    assert results[2].name == "Tear Sheet Link"
    assert str(results[3].symbol) == "FUND2:EX"
    assert (results[3].asset_class, results[3].security_type) == (None, "Fund")


def test_extract_currency_heuristics(scraper):
    # FT's GBX suffix means pence, kept as GBX (#8)
    res = scraper._extract_currency("TICKER:GBX")
    assert res is not None
    assert str(res) == "GBX"

    res2 = scraper._extract_currency("AAA:BBB:CCC")
    assert res2 is not None
    assert str(res2) == "CCC"


def test_map_country_to_currency(scraper):
    assert str(scraper._map_country_to_currency("US")) == "USD"
    assert scraper._map_country_to_currency(None) is None


def test_map_country_to_code(scraper):
    assert scraper._map_country_to_code("United States") == "US"
    assert scraper._map_country_to_code("Invalid Country xyz") is None
    assert scraper._map_country_to_code(None) is None


def test_http_400_errors(scraper, client, serve_ft):
    serve_ft(client, {SEARCH: Reply(b"Error 400", 400), TEARSHEET_PATH: Reply(b"Error 400", 400)})
    with pytest.raises(requests.exceptions.HTTPError):
        scraper.search("AAPL")

    with pytest.raises(requests.exceptions.HTTPError):
        scraper.get_xid(Symbol(root="AAPL"))


def test_http_400_from_chart_api(scraper, client, serve_ft):
    adapter = serve_ft(client, {TEARSHEET_PATH: Reply(TEARSHEET), CHART: Reply(b"Error 400", 400)})
    with pytest.raises(requests.exceptions.HTTPError):
        scraper.get_history(Symbol(root="AAPL"), 10)
    # get_xid read XID 123 off the tearsheet before the chart API answered 400
    assert [e["Symbol"] for e in _posted(adapter.sent)[0]["elements"]] == ["123", "123"]
