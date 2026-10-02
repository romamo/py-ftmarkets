"""FTClient stays on markets.ft.com, encodes query values, and honours its network settings"""

import pytest
from pydantic_market_data.models import Symbol

from ftmarkets.client import FTClient
from ftmarkets.extract.scraper import Scraper

TEARSHEET = b'<html><body><div data-mod-config=\'{"xid":"123456"}\'></div></body></html>'


@pytest.mark.parametrize(
    "path", ["https://evil.example/data", "http://markets.ft.com/data", "//evil.example/x", "data"]
)
@pytest.mark.parametrize("method", ["get", "post"])
def test_anything_but_a_relative_path_is_refused(method, path, record_ft):
    client = FTClient()
    adapter = record_ft(client, b"")
    with pytest.raises(ValueError, match="starting with '/'"):
        getattr(client, method)(path)
    assert adapter.sent == []


def test_get_xid_encodes_the_symbol(record_ft):
    client = FTClient()
    adapter = record_ft(client, TEARSHEET)

    xid = Scraper(http_client=client).get_xid(Symbol(root="X&foo=bar"))

    assert xid.root == "123456"
    assert [sent.url for sent in adapter.sent] == [
        "https://markets.ft.com/data/equities/tearsheet/summary?s=X%26foo%3Dbar"
    ]


@pytest.mark.parametrize(
    "script",
    [
        'window.config = {"symbol": "X", "xid": "987654"};',
        'window.config = {"xid":987654};',
        "window.config = {'xid': '987654'};",
        "var xid = 987654;",
    ],
)
def test_get_xid_regex_fallback_finds_the_xid_in_a_script(script, record_ft):
    """No data-mod-config: the XID comes from the page's script, JSON keys quoted or not"""
    client = FTClient()
    record_ft(client, f"<html><body><script>{script}</script></body></html>".encode())

    assert Scraper(http_client=client).get_xid(Symbol(root="X")).root == "987654"


def test_search_encodes_the_query(record_ft):
    client = FTClient()
    adapter = record_ft(client, b"<html><body></body></html>")

    assert Scraper(http_client=client).search("A&s=B") == []

    assert [sent.url for sent in adapter.sent] == [
        "https://markets.ft.com/data/search?query=A%26s%3DB"
    ]


def test_proxies_and_ca_bundle_reach_the_session(record_ft):
    proxies = {"http": "http://proxy.example:3128", "https": "http://proxy.example:3128"}
    client = FTClient(proxies=proxies, verify="/etc/ca.pem")
    adapter = record_ft(client, b"")

    client.get("/data/search", params={"query": "AAPL"})

    assert adapter.sent[0].proxies == proxies
    assert adapter.sent[0].verify == "/etc/ca.pem"
    assert client.session.trust_env is False


def test_default_client_reads_the_environment():
    client = FTClient()
    assert client.session.trust_env is True
    assert client.session.verify is True
