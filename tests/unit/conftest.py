"""A recording transport for FTClient: mounted on its session through ``Session.mount``,
it answers every request with a fixed body and keeps what ``requests`` handed it"""

from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO

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


class RecordingAdapter(HTTPAdapter):
    def __init__(self, body: bytes):
        super().__init__()
        self.body = body
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
        if request.url is None:
            raise ValueError("request has no URL")
        self.sent.append(Sent(url=request.url, proxies=dict(proxies or {}), verify=verify))
        raw = HTTPResponse(
            body=BytesIO(self.body),
            status=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            preload_content=False,
        )
        return self.build_response(request, raw)


@pytest.fixture
def record_ft() -> Callable[[FTClient, bytes], RecordingAdapter]:
    """``record_ft(client, body)`` routes ``client``'s requests to a new RecordingAdapter"""

    def mount(client: FTClient, body: bytes) -> RecordingAdapter:
        adapter = RecordingAdapter(body)
        client.session.mount("https://", adapter)
        client.session.mount("http://", adapter)
        return adapter

    return mount
