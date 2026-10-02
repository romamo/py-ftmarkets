"""FT's failures end the run with declared exit codes, not HANDLER_CRASHED (#17)"""

from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from io import BytesIO

import pytest
import requests
from pydantic_market_data.models import (
    History,
    HistoryPeriod,
    Price,
    Security,
    SecurityQuery,
    Symbol,
)
from requests import PreparedRequest, Response
from requests.adapters import HTTPAdapter
from urllib3 import HTTPResponse

pytest.importorskip("treaty")

from treaty import App, Envelope, NetworkSettings  # noqa: E402

from ftmarkets.api import FTDataSource  # noqa: E402
from ftmarkets.cli_app import create_app, ft_client  # noqa: E402
from ftmarkets.client import FTClient  # noqa: E402
from ftmarkets.extract.scraper import Scraper, ScraperError  # noqa: E402

URL = "https://markets.ft.com/data/search?query=AAPL"
APPLE = Security(symbol="AAPL:NSQ", name="Apple Inc.", currency="USD", country="US")


class FailingSource:
    """A DataSource whose every FT call raises ``failure``"""

    def __init__(self, failure: Exception):
        self.failure = failure

    def search(self, query: str) -> list[Security]:
        raise self.failure

    def resolve(self, criteria: SecurityQuery) -> Security | None:
        raise self.failure

    def history(self, symbol: Symbol.Input, period: HistoryPeriod = HistoryPeriod.MO1) -> History:
        raise self.failure

    def get_price(self, symbol: Symbol.Input, date: date | None = None) -> Price | None:
        raise self.failure

    def validate(
        self,
        symbol: Symbol.Input,
        target_date: date,
        target_price: Price.Input,
        price_tolerance: float = 0.10,
    ) -> bool:
        raise self.failure


def http_error(status: int, headers: dict[str, str] | None = None) -> requests.HTTPError:
    """The HTTPError ``raise_for_status`` raises for an FT answer of ``status``"""
    request = requests.Request("GET", URL).prepare()
    response = Response()
    response.status_code = status
    response.headers.update(headers or {})
    response.url = URL
    response.request = request
    return requests.HTTPError(f"{status} Error for url: {URL}", response=response)


def transport(cls: type[requests.RequestException]) -> requests.RequestException:
    return cls("FT is down", request=requests.Request("GET", URL).prepare())


def run(failure: Exception, command: str) -> Envelope:
    app: App = create_app(lambda network: FailingSource(failure))
    return app.call(command, {"symbol": "AAPL"})


def error(envelope: Envelope) -> dict:
    found = envelope.to_json()["error"]
    assert isinstance(found, dict)
    return found


COMMANDS = pytest.mark.parametrize("command", ["lookup", "history"])


@COMMANDS
@pytest.mark.parametrize(
    ("failure", "exit_code", "code"),
    [
        (transport(requests.ConnectionError), 12, "CONNECTION_FAILED"),
        (transport(requests.exceptions.ProxyError), 12, "CONNECTION_FAILED"),
        (transport(requests.exceptions.ChunkedEncodingError), 12, "CONNECTION_FAILED"),
        (transport(requests.exceptions.RetryError), 12, "CONNECTION_FAILED"),
        (transport(requests.exceptions.ReadTimeout), 12, "UPSTREAM_TIMEOUT"),
        (transport(requests.exceptions.ConnectTimeout), 12, "UPSTREAM_TIMEOUT"),
        (http_error(500), 12, "UPSTREAM_UNAVAILABLE"),
        (http_error(503), 12, "UPSTREAM_UNAVAILABLE"),
        (http_error(429), 11, "RATE_LIMITED"),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, Exception) else str(value),
)
def test_transport_failures_are_retryable(command, failure, exit_code, code):
    envelope = run(failure, command)
    assert envelope.exit_code == exit_code
    assert error(envelope)["code"] == code
    assert error(envelope)["retryable"] is True
    assert error(envelope)["suggestion"]
    assert error(envelope)["context"]["symbol"] == "AAPL"
    assert error(envelope)["context"]["url"] == URL


@COMMANDS
@pytest.mark.parametrize(
    ("failure", "exit_code", "code"),
    [
        (ScraperError("Could not determine internal FT ID for ticker AAPL"), 80, "FT_PAGE_CHANGED"),
        (http_error(400), 80, "UPSTREAM_REJECTED"),
        (http_error(403), 80, "UPSTREAM_REJECTED"),
        (http_error(404), 80, "UPSTREAM_REJECTED"),
        (transport(requests.exceptions.SSLError), 4, "TLS_FAILED"),
    ],
    ids=["scraper", "400", "403", "404", "tls"],
)
def test_upstream_contract_failures_are_not_retryable(command, failure, exit_code, code):
    envelope = run(failure, command)
    assert envelope.exit_code == exit_code
    assert error(envelope)["code"] == code
    assert error(envelope)["retryable"] is False
    assert error(envelope)["context"]["symbol"] == "AAPL"


@COMMANDS
def test_scraper_error_says_to_report_it(command):
    envelope = run(ScraperError("Malformed chart response for AAPL:NSQ"), command)
    assert "github.com/romamo/py-ftmarkets/issues" in error(envelope)["suggestion"]
    assert error(envelope)["detail"] == "Malformed chart response for AAPL:NSQ"


@COMMANDS
@pytest.mark.parametrize(
    ("headers", "wait_ms"),
    [({"Retry-After": "7"}, 7000), ({}, 30_000), ({"Retry-After": "soon"}, 30_000)],
)
def test_rate_limit_passes_on_retry_after(command, headers, wait_ms):
    envelope = run(http_error(429, headers), command)
    assert error(envelope)["retry_after_ms"] == wait_ms
    assert error(envelope)["context"]["status_code"] == 429


def test_rate_limit_reads_an_http_date_retry_after():
    when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
    envelope = run(http_error(429, {"Retry-After": when}), "lookup")
    assert 100_000 <= error(envelope)["retry_after_ms"] <= 120_000


def test_unavailable_passes_on_retry_after():
    envelope = run(http_error(503, {"Retry-After": "5"}), "history")
    assert error(envelope)["retry_after_ms"] == 5000


@COMMANDS
@pytest.mark.parametrize(
    "failure",
    [TypeError("a bug"), requests.HTTPError("no response attached")],
    ids=["TypeError", "HTTPError without a response"],
)
def test_other_errors_still_crash(command, failure):
    envelope = run(failure, command)
    assert envelope.exit_code == 1
    assert error(envelope)["code"] == "HANDLER_CRASHED"
    assert error(envelope)["context"]["exception"] == type(failure).__name__


class StatusAdapter(HTTPAdapter):
    """Answers every request with ``status`` and ``headers``, retries left out"""

    def __init__(self, status: int, headers: dict[str, str]):
        super().__init__()
        self.status = status
        self.headers = headers

    def send(  # type: ignore[override]
        self,
        request: PreparedRequest,
        stream: bool = False,
        timeout: object = None,
        verify: bool | str = True,
        cert: object = None,
        proxies: dict[str, str] | None = None,
    ) -> Response:
        raw = HTTPResponse(
            body=BytesIO(b""), status=self.status, headers=self.headers, preload_content=False
        )
        return self.build_response(request, raw)


def test_a_real_429_from_ft_reaches_the_envelope():
    def source(network: NetworkSettings) -> FTDataSource:
        client = ft_client(network)
        client.session.mount("https://", StatusAdapter(429, {"Retry-After": "9"}))
        return FTDataSource(Scraper(http_client=client))

    envelope = create_app(source).call("lookup", {"isin": "US0378331005"})
    assert envelope.exit_code == 11
    assert error(envelope)["retry_after_ms"] == 9000
    assert error(envelope)["context"]["isin"] == "US0378331005"


def test_client_returns_the_last_response_once_status_retries_run_out():
    # So raise_for_status() raises an HTTPError with the status and Retry-After, not a
    # RetryError that has neither
    retries = FTClient().session.get_adapter(FTClient.BASE_URL).max_retries
    assert retries.raise_on_status is False
