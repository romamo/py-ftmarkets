from unittest.mock import MagicMock

import pytest
import requests

from ftmarkets.client import FTClient
from ftmarkets.extract.scraper import Scraper


@pytest.fixture
def mock_client():
    mock = MagicMock(spec=FTClient)
    return mock


@pytest.fixture
def scraper(mock_client):
    return Scraper(http_client=mock_client)


def test_search_http_error_handling(scraper, mock_client):
    # Mock response that raises HTTPError
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("Internal Server Error")
    mock_client.get.return_value = mock_resp

    with pytest.raises(requests.exceptions.HTTPError):
        scraper.search("TEST")


def test_get_history_http_error_handling(scraper, mock_client):
    # Mock get_xid success then chart API failure
    xid_html = """<div data-mod-config='{"xid":"111222"}'></div>"""
    mock_client.get.return_value = MagicMock(
        status_code=200, content=xid_html.encode(), text=xid_html
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = "Bad Request"
    mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("Bad Request")
    mock_client.post.return_value = mock_resp

    with pytest.raises(requests.exceptions.HTTPError):
        scraper.get_history("AAPL:NSQ")
