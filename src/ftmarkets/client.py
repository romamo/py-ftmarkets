from collections.abc import Mapping
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class FTClient:
    """
    Stateless client for markets.ft.com.

    ``proxies``, a ``requests``-style ``{"http": url, "https": url}`` mapping (empty for a
    direct connection), replaces the proxy environment variables when given; left as
    ``None``, ``requests`` reads them as usual. ``verify`` is ``requests``' TLS setting:
    ``True`` for the default CA store, or the path of a CA bundle.
    """

    BASE_URL = "https://markets.ft.com"

    def __init__(
        self,
        proxies: Mapping[str, str] | None = None,
        verify: bool | str = True,
    ):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://markets.ft.com/data/equities",
            }
        )
        if proxies is not None:
            # Explicit settings win: the environment's proxies and CA bundle are not read
            self.session.trust_env = False
            self.session.proxies = dict(proxies)
        self.session.verify = verify

        # Retry strategy
        retries = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "OPTIONS"],
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _url(self, path: str) -> str:
        """``path`` on markets.ft.com. Anything but a ``/``-relative path is refused, so no
        caller can send the session, its headers, or its proxy settings to another host"""
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError(f"FTClient takes a path starting with '/', not {path!r}")
        return f"{self.BASE_URL}{path}"

    def get(self, path: str, params: dict[str, Any] | None = None, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", 10)  # default 10s timeout
        return self.session.get(self._url(path), params=params, **kwargs)

    def post(self, path: str, json: dict[str, Any] | None = None, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", 10)
        return self.session.post(self._url(path), json=json, **kwargs)


# Singleton instance
client = FTClient()
