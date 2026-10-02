"""Scraper robustness against odd chart dates, search URLs, and tearsheet configs (#19),
through an injected FTClient fake"""

import json
from typing import Any

import pytest
import requests

from ftmarkets.client import FTClient
from ftmarkets.extract.schemas import Symbol
from ftmarkets.extract.scraper import Scraper, ScraperError

_XID_PAGE = """<div data-mod-config='{"xid":"111222"}'></div>"""


def _response(content: str, url: str) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response._content = content.encode()
    response.encoding = "utf-8"
    response.url = url
    return response


class FakeClient(FTClient):
    """Serves ``page`` at ``url`` for every GET and ``chart`` for the chart POST"""

    def __init__(
        self,
        page: str = _XID_PAGE,
        url: str = "https://markets.ft.com/data/equities/tearsheet/summary?s=X",
        chart: dict[str, Any] | None = None,
    ) -> None:
        self.page = page
        self.url = url
        self.chart_body = json.dumps(chart or {})

    def get(self, path: str, params: dict[str, Any] | None = None, **kwargs) -> requests.Response:
        return _response(self.page, self.url)

    def post(self, path: str, json: dict[str, Any] | None = None, **kwargs) -> requests.Response:
        return _response(self.chart_body, self.url)


def _chart(dates: list[str]) -> dict[str, Any]:
    n = len(dates)
    series = [
        {"Type": kind, "Values": [100.0 + i for i in range(n)]}
        for kind in ("Open", "High", "Low", "Close")
    ]
    return {
        "Dates": dates,
        "Elements": [{"Type": "price", "Symbol": "111222", "ComponentSeries": series}],
    }


# --- 1. Chart dates History rejects ---


@pytest.mark.parametrize(
    "dates",
    [
        pytest.param(["2025-01-02T00:00:00", "2025-01-02T00:00:00"], id="duplicate"),
        pytest.param(["2025-01-03T00:00:00", "2025-01-02T00:00:00"], id="out-of-order"),
        pytest.param(["2025-01-02T00:00:00", "2025-01-03T00:00:00Z"], id="mixed-timezone"),
    ],
)
def test_chart_dates_history_rejects_raise_scraper_error(dates):
    scraper = Scraper(http_client=FakeClient(chart=_chart(dates)))
    with pytest.raises(ScraperError, match="Malformed chart response for AAPL:NSQ"):
        scraper.get_history(Symbol(root="AAPL:NSQ"), days=5)


# --- 2. Tearsheet detection looks at the path only ---

_SEARCH_PAGE = """
<html><div id="equity-panel" role="tabpanel">
<table class="mod-ui-table"><tbody>
<tr>
  <td><a href="/data/equities/tearsheet/summary?s=TSH:LSE">Tearsheet Holdings PLC</a></td>
  <td>TSH:LSE</td>
  <td>London Stock Exchange</td>
  <td>United Kingdom</td>
</tr>
</tbody></table>
</div></html>
"""


def test_search_for_tearsheet_parses_the_results_table():
    client = FakeClient(page=_SEARCH_PAGE, url="https://markets.ft.com/data/search?query=tearsheet")
    results = Scraper(http_client=client).search("tearsheet")
    assert [str(r.symbol) for r in results] == ["TSH:LSE"]
    assert results[0].name == "Tearsheet Holdings PLC"


def test_tearsheet_redirect_is_still_parsed_as_a_tearsheet():
    page = '<html><h1 class="mod-tearsheet-overview__header__name">Apple Inc</h1></html>'
    client = FakeClient(
        page=page, url="https://markets.ft.com/data/equities/tearsheet/summary?s=AAPL:NSQ"
    )
    results = Scraper(http_client=client).search("AAPL")
    assert [str(r.symbol) for r in results] == ["AAPL:NSQ"]


# --- 3. data-mod-config parsing ---

# Shaped like the live ETF tearsheet (VUSA:LSE:GBP): a holdings list, a chart keyed by
# issueID, and an alert module before the watchlist module that holds the xid
_LIVE_SHAPED_PAGE = """<html><body>
<div class="mod-top-ten" data-mod-config='[[{"Label":"Others","Percent":100}]]'></div>
<div class="mod-symbol-chart" data-mod-config='{"isStatic":false,"issueID":"46487963"}'></div>
<section class="mod-tearsheet-add-alert"
  data-mod-config='{"issueId":"46487963","assetClass":"ETF"}'></section>
<section class="mod-tearsheet-add-to-watchlist"
  data-mod-config='{"xid":"46487963","assetClass":"ETF","symbol":"VUSA:LSE:GBP"}'></section>
<div data-mod-config='not json at all'></div>
</body></html>"""


def _xid(page: str) -> str:
    return Scraper(http_client=FakeClient(page=page)).get_xid(Symbol(root="X")).root


def test_unrelated_module_configs_are_skipped():
    assert _xid(_LIVE_SHAPED_PAGE) == "46487963"


def test_integer_xid_is_accepted():
    assert _xid("""<div data-mod-config='{"xid":7}'></div>""") == "7"


@pytest.mark.parametrize(
    "config",
    [
        pytest.param('{"xid":null}', id="null"),
        pytest.param('{"xid":true}', id="bool"),
        pytest.param('{"xid":1.5}', id="float"),
        pytest.param('{"xid":["1"]}', id="list"),
        pytest.param('{"xid":""}', id="empty"),
        pytest.param('"xid-x"', id="json-string"),
        pytest.param('["xid", 1]', id="json-list"),
        pytest.param("{xid: 1", id="not-json"),
    ],
)
def test_malformed_xid_config_raises(config):
    page = f"<div data-mod-config='{config}'></div><script>var c = {{ xid: 987654 }};</script>"
    with pytest.raises(ScraperError, match="data-mod-config"):
        _xid(page)


@pytest.mark.parametrize(
    "config",
    [
        pytest.param("7", id="json-number"),
        pytest.param('"plain"', id="json-string"),
        pytest.param('{"issueID":"1"}', id="no-xid"),
    ],
)
def test_config_without_top_level_xid_falls_through_to_the_regex(config):
    page = f"<div data-mod-config='{config}'></div><script>var c = {{ xid: 987654 }};</script>"
    assert _xid(page) == "987654"


# --- 4. Regex word boundary ---


def test_regex_does_not_match_xid_inside_a_word():
    page = "<script>var maxid = 42;</script>"
    with pytest.raises(ScraperError, match="Could not determine internal FT ID"):
        _xid(page)


def test_regex_still_matches_a_bare_xid():
    assert _xid("<script>var maxid = 42; xid = 987654;</script>") == "987654"
