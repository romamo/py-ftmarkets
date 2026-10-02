"""treaty's --proxy, --no-proxy, proxy variables, and CA bundle reach FTClient's session"""

from collections.abc import Callable

import pytest

pytest.importorskip("treaty")

from conftest import RecordingAdapter, Sent  # noqa: E402
from treaty import App, NetworkSettings  # noqa: E402

from ftmarkets.api import FTDataSource  # noqa: E402
from ftmarkets.cli_app import create_app, ft_client  # noqa: E402
from ftmarkets.client import FTClient  # noqa: E402
from ftmarkets.extract.scraper import Scraper  # noqa: E402

PROXY = "http://proxy.example:3128"
NO_RESULTS = b"<html><body></body></html>"


class RecordingSources:
    """A source factory building the real FT source from ``ctx.network``, its requests
    answered by a RecordingAdapter"""

    def __init__(self, record_ft: Callable[[FTClient, bytes], RecordingAdapter]):
        self.record_ft = record_ft
        self.adapters: list[RecordingAdapter] = []

    def __call__(self, network: NetworkSettings) -> FTDataSource:
        client = ft_client(network)
        self.adapters.append(self.record_ft(client, NO_RESULTS))
        return FTDataSource(Scraper(http_client=client))

    def sent(self) -> list[Sent]:
        return [sent for adapter in self.adapters for sent in adapter.sent]


@pytest.fixture
def sources(record_ft) -> RecordingSources:
    return RecordingSources(record_ft)


def lookup(app: App, env: dict[str, str], **framework: object):
    return app.call("lookup", {"symbol": "AAPL", **framework}, env=env)


def test_proxy_variable_reaches_the_session(sources):
    envelope = lookup(create_app(sources), {"HTTPS_PROXY": PROXY})
    assert envelope.exit_code == 5  # no results: the request went out and came back
    [sent] = sources.sent()
    assert sent.url == "https://markets.ft.com/data/search?query=AAPL"
    assert sent.proxies == {"http": PROXY, "https": PROXY}
    assert sent.verify is True


def test_proxy_flag_overrides_the_variables(sources):
    lookup(create_app(sources), {"HTTPS_PROXY": PROXY}, proxy="http://other.example:8080")
    [sent] = sources.sent()
    assert sent.proxies == {
        "http": "http://other.example:8080",
        "https": "http://other.example:8080",
    }


def test_no_proxy_flag_connects_directly(sources):
    lookup(create_app(sources), {"HTTPS_PROXY": PROXY}, no_proxy=True)
    [sent] = sources.sent()
    assert sent.proxies == {}


def test_no_proxy_variable_naming_ft_connects_directly(sources):
    lookup(create_app(sources), {"HTTPS_PROXY": PROXY, "NO_PROXY": "markets.ft.com"})
    [sent] = sources.sent()
    assert sent.proxies == {}


def test_ca_bundle_reaches_the_session(sources, tmp_path):
    bundle = tmp_path / "ca.pem"
    bundle.write_text("")
    lookup(create_app(sources), {"REQUESTS_CA_BUNDLE": str(bundle)})
    [sent] = sources.sent()
    assert sent.verify == str(bundle)
    assert sent.proxies == {}
