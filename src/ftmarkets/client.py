from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class FTClient:
    """
    Stateless client for markets.ft.com.
    """

    BASE_URL = "https://markets.ft.com"

    def __init__(self):
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

    def get(self, path: str, params: dict[str, Any] | None = None, **kwargs) -> requests.Response:
        url = f"{self.BASE_URL}{path}" if path.startswith("/") else path
        # If full URL passed (e.g. for scraping), handle it
        if path.startswith("http"):
            url = path

        kwargs.setdefault("timeout", 10)  # default 10s timeout
        return self.session.get(url, params=params, **kwargs)

    def post(self, path: str, json: dict[str, Any] | None = None, **kwargs) -> requests.Response:
        url = f"{self.BASE_URL}{path}"
        kwargs.setdefault("timeout", 10)
        return self.session.post(url, json=json, **kwargs)


# Singleton instance
client = FTClient()
