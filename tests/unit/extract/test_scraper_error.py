"""HTTP errors from FT propagate out of Scraper; served to a real FTClient through
RecordingAdapter"""

import pytest
import requests
from conftest import Reply

from ftmarkets.client import FTClient
from ftmarkets.extract.scraper import Scraper


def test_search_http_error_handling(serve_ft):
    client = FTClient()
    serve_ft(client, {"/data/search": Reply(b"Internal Server Error", 500)})

    with pytest.raises(requests.exceptions.HTTPError):
        Scraper(http_client=client).search("TEST")


def test_get_history_http_error_handling(serve_ft):
    # get_xid succeeds, then the chart API fails
    client = FTClient()
    serve_ft(
        client,
        {
            "/data/equities/tearsheet/summary": Reply(
                b"""<div data-mod-config='{"xid":"111222"}'></div>"""
            ),
            "/data/chartapi/series": Reply(b"Bad Request", 400),
        },
    )

    with pytest.raises(requests.exceptions.HTTPError):
        Scraper(http_client=client).get_history("AAPL:NSQ")
