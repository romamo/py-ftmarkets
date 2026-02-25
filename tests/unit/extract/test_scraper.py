from unittest.mock import MagicMock

import pytest
from pydantic_market_data.models import Symbol

from ftmarkets.client import FTClient
from ftmarkets.extract.schemas import Ticker
from ftmarkets.extract.scraper import Scraper, Xid


@pytest.fixture
def mock_client():
    mock = MagicMock(spec=FTClient)
    # Mock responses
    mock.get.return_value = MagicMock(status_code=200, content=b"<html></html>", text="")
    mock.post.return_value = MagicMock(status_code=200, json=lambda: {})
    return mock


@pytest.fixture
def scraper(mock_client):
    return Scraper(http_client=mock_client)


def test_get_xid_extraction(scraper, mock_client):
    # Setup mock HTML with xid in data-mod-config
    html_content = """
    <html>
        <body>
            <div data-mod-config='{"xid":"123456", "symbol":"TEST"}'></div>
        </body>
    </html>
    """
    mock_client.get.return_value = MagicMock(
        status_code=200, content=html_content.encode(), text=html_content
    )

    xid = scraper.get_xid(Ticker(root="TEST:EX"))

    assert isinstance(xid, Xid)
    assert xid.root == "123456"
    assert str(xid) == "123456"
    mock_client.get.assert_called_with("/data/equities/tearsheet/summary?s=TEST:EX")


def test_get_xid_regex_fallback(scraper, mock_client):
    # Setup mock HTML with xid in script or other text
    html_content = """
    <html>
        <script>
            var config = { xid: "987654" };
        </script>
    </html>
    """
    mock_client.get.return_value = MagicMock(
        status_code=200, content=html_content.encode(), text=html_content
    )

    xid = scraper.get_xid(Ticker(root="TEST:REGEX"))

    assert xid.root == "987654"


def test_search_parsing(scraper, mock_client):
    # Mock search results HTML
    html_content = """
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
    mock_client.get.return_value = MagicMock(status_code=200, content=html_content.encode())

    results = scraper.search("AAPL")

    assert len(results) == 1
    sym = results[0]
    assert isinstance(sym, Symbol)
    assert str(sym.ticker) == "AAPL:NSQ"
    assert sym.name == "Apple Inc"
    assert str(sym.country) == "US"
    assert sym.asset_class == "Equity"


def test_get_history(scraper, mock_client):
    # 1. Mock get_xid call
    # We can rely on internal logic or just mock get_xid if we want unit test isolation
    # But since Scraper calls get_xid internally, let's mock the network call for xid first
    xid_html = """<div data-mod-config='{"xid":"111222"}'></div>"""

    # 2. Mock chart response
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

    # Side effect for get/post to return different things
    # get -> xid page, post -> chart data
    def side_effect(*args, **kwargs):
        return MagicMock(status_code=200, json=lambda: chart_json)

    mock_client.post.side_effect = side_effect
    mock_client.get.return_value = MagicMock(
        status_code=200, content=xid_html.encode(), text=xid_html
    )

    hist = scraper.get_history(Ticker(root="AAPL:NSQ"), days=10)

    assert len(hist.candles) == 1
    candle = hist.candles[0]
    assert candle.close == 105.0
    assert candle.volume == 5000

    # Verify strict request payload
    calls = mock_client.post.call_args_list
    assert len(calls) == 1
    payload = calls[0].kwargs["json"]
    assert payload["days"] == 10
    assert payload["elements"][0]["Symbol"] == "111222"


def test_search_tearsheet_redirect(scraper, mock_client):
    # Mock redirect to tearsheet
    html_content = """
    <html>
        <h1 class="mod-tearsheet-overview__header__name">Apple Inc</h1>
        <table><tr><th>ISIN</th><td>US0378331005</td></tr></table>
    </html>
    """
    mock_url = "https://markets.ft.com/data/equities/tearsheet/summary?s=AAPL:NSQ"
    mock_client.get.return_value = MagicMock(
        status_code=200, content=html_content.encode(), url=mock_url, text=html_content
    )

    results = scraper.search("US0378331005")

    assert len(results) == 1
    assert str(results[0].ticker) == "AAPL:NSQ"
    assert str(results[0].isin) == "US0378331005"


def test_get_xid_fails(scraper, mock_client):
    from ftmarkets.extract.scraper import ScraperError

    mock_client.get.return_value = MagicMock(
        status_code=200, content=b"no xid here", text="no xid here"
    )

    with pytest.raises(ScraperError, match="Could not determine internal FT ID"):
        scraper.get_xid(Ticker(root="UNKNOWN"))


def test_extract_currency_strict(scraper):

    curr = scraper._extract_currency("TICKER:EXCHANGE:USD")
    assert str(curr) == "USD"

    assert scraper._extract_currency("INVALID") is None


def test_search_parsing_funds_and_etfs(scraper, mock_client):
    html_content = """
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
    mock_client.get.return_value = MagicMock(status_code=200, content=html_content.encode())
    results = scraper.search("TEST")

    assert len(results) == 4
    assert str(results[0].ticker) == "FUND:EX"
    assert results[0].asset_class == "Fund"
    assert str(results[1].ticker) == "IDX:EX"
    assert results[1].asset_class == "Index"
    assert str(results[2].ticker) == "TEAR:SHEET"
    assert results[2].name == "Tear Sheet Link"
    assert str(results[3].ticker) == "FUND2:EX"
    assert results[3].asset_class == "Fund"


def test_extract_currency_heuristics(scraper):
    res = scraper._extract_currency("TICKER:GBX")
    assert res is not None
    assert str(res) == "GBP"

    res2 = scraper._extract_currency("AAA:BBB:CCC")
    assert res2 is not None
    assert str(res2) == "CCC"


def test_map_country_to_currency(scraper):
    assert str(scraper._map_country_to_currency("US")) == "USD"
    assert scraper._map_country_to_currency(None) is None
