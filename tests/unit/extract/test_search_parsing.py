"""Search and XID parsing against fixture HTML, with an injected fake FTClient (#7)."""

from dataclasses import dataclass, field
from typing import Any

import pytest

from ftmarkets.extract.schemas import Symbol
from ftmarkets.extract.scraper import Scraper, ScraperError

SEARCH_URL = "https://markets.ft.com/data/search?query=x"
TEARSHEET_URL = "https://markets.ft.com/data/equities/tearsheet/summary?s=AAPL:NSQ"

# Shaped like the live equity panel: a "Primary" badge inside the exchange cell
SEARCH_PAGE = """
<html><div id="equity-panel" role="tabpanel">
<table class="mod-ui-table"><tbody>
<tr data-mod-primary="true">
  <td><a href="/data/equities/tearsheet/summary?s=AAPL:NSQ">Apple Inc.</a></td>
  <td>AAPL:NSQ</td>
  <td>Consolidated Issue Listed on NASDAQ Global Select
    <span class="primary-tag">Primary</span></td>
  <td>United States</td>
</tr>
<tr>
  <td><a href="/data/equities/tearsheet/summary?s=VOD:LSE">Vodafone Group PLC</a></td>
  <td>VOD:LSE</td>
  <td>London Stock Exchange<span class="primary-tag">Primary</span></td>
  <td>United Kingdom</td>
</tr>
</tbody></table>
</div>
<a href="/data/equities/tearsheet/summary?s=MSFT:NSQ">Microsoft Corp</a>
</html>
"""

TEARSHEET_PAGE = """
<html>
  <h1 class="mod-tearsheet-overview__header__name">Apple Inc</h1>
  <table><tr><th>ISIN</th><td>US0378331005</td></tr></table>
</html>
"""

TEARSHEET_PAGE_NO_ISIN = """
<html><h1 class="mod-tearsheet-overview__header__name">Apple Inc</h1></html>
"""


@dataclass
class FakeResponse:
    content: bytes
    url: str
    status_code: int = 200

    @property
    def text(self) -> str:
        return self.content.decode()

    def raise_for_status(self) -> None:
        return None


@dataclass
class FakeClient:
    """Answers every GET with one canned page and records the calls."""

    page: str
    url: str = SEARCH_URL
    calls: list[tuple[str, dict[str, Any] | None]] = field(default_factory=list)

    def get(self, path: str, params: dict[str, Any] | None = None) -> FakeResponse:
        self.calls.append((path, params))
        return FakeResponse(content=self.page.encode(), url=self.url)


def make_scraper(page: str, url: str = SEARCH_URL) -> Scraper:
    return Scraper(http_client=FakeClient(page=page, url=url))  # type: ignore[arg-type]


def test_non_isin_twelve_char_query_does_not_crash():
    # Two letters + ten alphanumerics used to be stamped as an ISIN and fail validation
    results = make_scraper(SEARCH_PAGE).search("AMAZONCOMINC")
    assert [str(r.symbol) for r in results] == ["AAPL:NSQ", "VOD:LSE", "MSFT:NSQ"]
    assert all(r.isin is None for r in results)


def test_isin_query_tags_table_rows_but_not_link_rows():
    results = make_scraper(SEARCH_PAGE).search("US0378331005")
    assert {str(r.symbol): (str(r.isin) if r.isin else None) for r in results} == {
        "AAPL:NSQ": "US0378331005",
        "VOD:LSE": "US0378331005",
        "MSFT:NSQ": None,
    }


def test_isin_query_failing_checksum_tags_no_rows():
    results = make_scraper(SEARCH_PAGE).search("US0378331002")
    assert len(results) == 3
    assert all(r.isin is None for r in results)


def test_exchange_cell_excludes_primary_badge():
    results = make_scraper(SEARCH_PAGE).search("VOD")
    assert [r.exchange for r in results] == [
        "Consolidated Issue Listed on NASDAQ Global Select",
        "London Stock Exchange",
        None,
    ]


def test_h3_header_with_child_element():
    page = """
    <html><div class="mod-search-results__section">
      <h3><span class="icon"></span>Indices</h3>
      <table class="mod-ui-table"><tbody>
        <tr><td>FTSE 100</td><td>FTSE:FSI</td></tr>
      </tbody></table>
    </div></html>
    """
    (result,) = make_scraper(page).search("FTSE")
    assert result.security_type == "Index"


def test_tearsheet_takes_isin_from_page():
    (result,) = make_scraper(TEARSHEET_PAGE, TEARSHEET_URL).search("AAPL")
    assert str(result.isin) == "US0378331005"


def test_tearsheet_falls_back_to_isin_query():
    (result,) = make_scraper(TEARSHEET_PAGE_NO_ISIN, TEARSHEET_URL).search("US0378331005")
    assert str(result.isin) == "US0378331005"


def test_tearsheet_ignores_query_failing_isin_checksum():
    # Right shape, wrong check digit
    (result,) = make_scraper(TEARSHEET_PAGE_NO_ISIN, TEARSHEET_URL).search("US0378331002")
    assert result.isin is None


def test_tearsheet_ignores_non_isin_query():
    (result,) = make_scraper(TEARSHEET_PAGE_NO_ISIN, TEARSHEET_URL).search("AMAZONCOMINC")
    assert result.isin is None


def test_tearsheet_with_invalid_isin_on_page_fails():
    page = TEARSHEET_PAGE.replace("US0378331005", "NOT-AN-ISIN")
    with pytest.raises(ScraperError, match="invalid ISIN"):
        make_scraper(page, TEARSHEET_URL).search("AAPL")


def test_get_xid_from_data_mod_config():
    page = """<html><div data-mod-config='{"xid":"123456","symbol":"AAPL:NSQ"}'></div></html>"""
    fake = FakeClient(page=page)
    xid = Scraper(http_client=fake).get_xid(Symbol(root="AAPL:NSQ"))  # type: ignore[arg-type]
    assert xid.root == "123456"
    assert fake.calls == [("/data/equities/tearsheet/summary", {"s": "AAPL:NSQ"})]


def test_get_xid_falls_back_to_regex():
    page = """
    <html>
      <div data-mod-config='not json'></div>
      <div data-mod-config='{"other":"1"}'></div>
      <script>var cfg = { xid: "987654" };</script>
    </html>
    """
    assert make_scraper(page).get_xid(Symbol(root="AAPL:NSQ")).root == "987654"


def test_get_xid_missing_raises():
    with pytest.raises(ScraperError, match="Could not determine internal FT ID"):
        make_scraper("<html><p>nothing</p></html>").get_xid(Symbol(root="AAPL:NSQ"))
