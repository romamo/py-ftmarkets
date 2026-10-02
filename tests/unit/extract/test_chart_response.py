"""Scraper.get_history against canned chart JSON, through an injected FTClient fake"""

import json
from typing import Any

import pytest
import requests

from ftmarkets.client import FTClient
from ftmarkets.extract.schemas import Symbol
from ftmarkets.extract.scraper import Scraper, ScraperError

_XID_PAGE = b"""<div data-mod-config='{"xid":"111222"}'></div>"""
_DATES = ["2025-01-14T00:00:00", "2025-01-15T00:00:00"]


def _response(content: bytes) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response._content = content
    response.encoding = "utf-8"
    return response


class FakeClient(FTClient):
    """Serves a tearsheet with an XID for every GET and ``chart`` for the chart POST"""

    def __init__(self, chart: dict[str, Any] | bytes) -> None:
        self.chart = chart if isinstance(chart, bytes) else json.dumps(chart).encode()

    def get(self, path: str, params: dict[str, Any] | None = None, **kwargs) -> requests.Response:
        return _response(_XID_PAGE)

    def post(self, path: str, json: dict[str, Any] | None = None, **kwargs) -> requests.Response:
        return _response(self.chart)


def _history(chart: dict[str, Any] | bytes):
    return Scraper(http_client=FakeClient(chart)).get_history(Symbol(root="AAPL:NSQ"), days=5)


def _price(n: int, skip: str | None = None) -> dict[str, Any]:
    series = [
        {"Type": kind, "Values": [100.0 + i for i in range(n)]}
        for kind in ("Open", "High", "Low", "Close")
        if kind != skip
    ]
    return {"Type": "price", "Symbol": "111222", "ComponentSeries": series}


def _volume(n: int) -> dict[str, Any]:
    return {
        "Type": "volume",
        "Symbol": "111222",
        "ComponentSeries": [{"Type": "Volume", "Values": [1000.0] * n}],
    }


def test_well_formed_response_gives_one_candle_per_date():
    hist = _history({"Dates": _DATES, "Elements": [_price(2), _volume(2)]})
    assert [c.close for c in hist.candles] == [100.0, 101.0]
    assert [c.volume for c in hist.candles] == [1000.0, 1000.0]


def test_missing_volume_element_leaves_volume_empty():
    hist = _history({"Dates": _DATES, "Elements": [_price(2)]})
    assert [c.volume for c in hist.candles] == [None, None]


@pytest.mark.parametrize(
    "elements",
    [[], [_price(0), _volume(0)], [_volume(0)]],
    ids=["no-elements", "empty-series", "volume-only"],
)
def test_no_dates_is_no_data(elements):
    hist = _history({"Dates": [], "Elements": elements})
    assert hist.candles == []
    assert str(hist.security.symbol) == "AAPL:NSQ"


@pytest.mark.parametrize(
    ("chart", "message"),
    [
        ({"Dates": _DATES, "Elements": []}, "no price element"),
        ({"Dates": _DATES, "Elements": [_volume(2)]}, "no price element"),
        ({"Dates": _DATES, "Elements": [_price(2, skip="Close")]}, "no Close series"),
        ({"Dates": _DATES, "Elements": [_price(1), _volume(2)]}, "1 Open values for 2 dates"),
        ({"Dates": _DATES, "Elements": [_price(2), _volume(1)]}, "1 Volume values for 2"),
        ({"Dates": [], "Elements": [_price(1)]}, "1 Open values for 0 dates"),
        ({"Elements": []}, "Malformed chart response"),
        ({"Dates": _DATES, "Elements": [{"Symbol": "111222"}]}, "Malformed chart response"),
        (b"<html>not json</html>", "Malformed chart response"),
    ],
    ids=[
        "dates-without-elements",
        "dates-without-price",
        "missing-close",
        "short-price-series",
        "short-volume-series",
        "values-without-dates",
        "no-dates-key",
        "element-without-type",
        "not-json",
    ],
)
def test_malformed_response_raises(chart, message):
    with pytest.raises(ScraperError, match=message):
        _history(chart)
