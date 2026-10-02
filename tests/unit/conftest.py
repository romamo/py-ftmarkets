"""A recording transport for FTClient: mounted on its session through ``Session.mount``,
it answers each request with a canned reply and keeps what ``requests`` handed it"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlsplit

import pytest
from requests import PreparedRequest, Response
from requests.adapters import HTTPAdapter
from urllib3 import HTTPResponse

from ftmarkets.client import FTClient


@dataclass(frozen=True)
class Sent:
    url: str
    proxies: dict[str, str]
    verify: bool | str
    method: str = "GET"
    body: bytes | None = None


@dataclass(frozen=True)
class Reply:
    """A canned answer. ``url`` is the final URL after FT's redirect (the request's URL
    when ``None``), as when an exact search match lands on a tearsheet"""

    body: bytes
    status: int = 200
    url: str | None = None


class RecordingAdapter(HTTPAdapter):
    """Answers a request by its URL path from ``routes``, else with ``default``; a request
    neither covers fails the test"""

    def __init__(self, routes: Mapping[str, Reply], default: Reply | None = None):
        super().__init__()
        self.routes = dict(routes)
        self.default = default
        self.sent: list[Sent] = []

    def send(  # type: ignore[override]
        self,
        request: PreparedRequest,
        stream: bool = False,
        timeout: object = None,
        verify: bool | str = True,
        cert: object = None,
        proxies: dict[str, str] | None = None,
    ) -> Response:
        if request.url is None or request.method is None:
            raise ValueError("request has no URL or method")
        body = request.body.encode() if isinstance(request.body, str) else request.body
        self.sent.append(
            Sent(
                url=request.url,
                proxies=dict(proxies or {}),
                verify=verify,
                method=request.method,
                body=body,
            )
        )
        path = urlsplit(request.url).path
        reply = self.routes.get(path, self.default)
        if reply is None:
            raise LookupError(f"no canned reply for {request.method} {path}")
        raw = HTTPResponse(
            body=BytesIO(reply.body),
            status=reply.status,
            headers={"Content-Type": "text/html; charset=utf-8"},
            preload_content=False,
        )
        response = self.build_response(request, raw)
        if reply.url is not None:
            response.url = reply.url
        return response


def _mount(client: FTClient, adapter: RecordingAdapter) -> RecordingAdapter:
    client.session.mount("https://", adapter)
    client.session.mount("http://", adapter)
    return adapter


@pytest.fixture
def record_ft() -> Callable[[FTClient, bytes], RecordingAdapter]:
    """``record_ft(client, body)`` answers every request of ``client`` with ``body``"""

    def mount(client: FTClient, body: bytes) -> RecordingAdapter:
        return _mount(client, RecordingAdapter({}, default=Reply(body)))

    return mount


@pytest.fixture
def serve_ft() -> Callable[[FTClient, Mapping[str, Reply]], RecordingAdapter]:
    """``serve_ft(client, {path: Reply(...)})`` answers ``client``'s requests by path"""

    def mount(client: FTClient, routes: Mapping[str, Reply]) -> RecordingAdapter:
        return _mount(client, RecordingAdapter(routes))

    return mount
