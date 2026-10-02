"""FTDataSource.resolve() applies the symbol alongside an ISIN or FIGI (#20), against a
fake scraper (no network, no patching)"""

import pytest
from pydantic_market_data.models import Security, SecurityQuery, Symbol

from ftmarkets.api import FTDataSource
from ftmarkets.extract.scraper import Scraper

ISIN = "DE000A0S9GB0"
FIGI = "BBG000BLNNH6"


def _sec(symbol: str) -> Security:
    return Security(symbol=symbol, name="Xetra-Gold")


# FT lists 4GLD on several exchanges; an ISIN search returns them all, Frankfurt first
_LISTINGS = [_sec("4GLD:GER:EUR"), _sec("4GLD:LSE:GBX"), _sec("4GLD:LSE"), _sec("4GLD:FRA:EUR")]


class FakeScraper(Scraper):
    """Answers each query from ``results`` (no hits otherwise) and records the queries"""

    def __init__(self, results: dict[str, list[Security]]) -> None:
        self.results = results
        self.queries: list[str] = []

    def search(self, query: Symbol.Input) -> list[Security]:
        self.queries.append(str(query))
        return list(self.results.get(str(query), []))


def _resolve(results: dict[str, list[Security]], **query: str) -> tuple[str | None, list[str]]:
    fake = FakeScraper(results)
    found = FTDataSource(scraper_instance=fake).resolve(SecurityQuery(**query))
    return (str(found.symbol) if found else None), fake.queries


def test_isin_and_symbol_pick_that_listing():
    found, queries = _resolve({ISIN: _LISTINGS}, isin=ISIN, symbol="4GLD:LSE:GBX")
    assert found == "4GLD:LSE:GBX"
    assert queries == [ISIN]


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        pytest.param("4gld:lse:gbx", "4GLD:LSE:GBX", id="case-insensitive"),
        pytest.param("4GLD:LSE", "4GLD:LSE", id="exact-before-prefix"),
        pytest.param("4GLD:FRA", "4GLD:FRA:EUR", id="ticker-exchange-matches-with-currency"),
    ],
)
def test_symbol_matching_rules(symbol, expected):
    found, _ = _resolve({ISIN: _LISTINGS}, isin=ISIN, symbol=symbol)
    assert found == expected


def test_symbol_with_currency_does_not_match_listing_without_it():
    """A currency the caller named is not dropped: 4GLD:NSQ:USD is not 4GLD:NSQ"""
    results = {ISIN: [_sec("4GLD:NSQ")], "4GLD:NSQ:USD": [_sec("4GLD:NSQ:USD")]}
    found, queries = _resolve(results, isin=ISIN, symbol="4GLD:NSQ:USD")
    assert found == "4GLD:NSQ:USD"
    assert queries == [ISIN, "4GLD:NSQ:USD"]


def test_partial_exchange_does_not_match():
    found, queries = _resolve({ISIN: _LISTINGS}, isin=ISIN, symbol="4GLD:LS")
    assert found is None
    assert queries == [ISIN, "4GLD:LS"]


def test_symbol_not_among_isin_hits_falls_back_to_symbol_search():
    results = {ISIN: _LISTINGS, "4GLD:MIL:EUR": [_sec("4GLD:MIL:EUR")]}
    found, queries = _resolve(results, isin=ISIN, symbol="4GLD:MIL:EUR")
    assert found == "4GLD:MIL:EUR"
    assert queries == [ISIN, "4GLD:MIL:EUR"]


def test_figi_and_symbol_pick_that_listing():
    found, queries = _resolve({FIGI: _LISTINGS}, figi=FIGI, symbol="4GLD:LSE:GBX")
    assert found == "4GLD:LSE:GBX"
    assert queries == [FIGI]


def test_symbol_not_among_figi_hits_tries_isin_then_symbol():
    results = {
        FIGI: [_sec("4GLD:GER:EUR")],
        ISIN: _LISTINGS,
        "4GLD:MIL:EUR": [_sec("4GLD:MIL:EUR")],
    }
    found, queries = _resolve(results, figi=FIGI, isin=ISIN, symbol="4GLD:LSE:GBX")
    assert found == "4GLD:LSE:GBX"
    assert queries == [FIGI, ISIN]

    found, queries = _resolve(results, figi=FIGI, isin=ISIN, symbol="4GLD:MIL:EUR")
    assert found == "4GLD:MIL:EUR"
    assert queries == [FIGI, ISIN, "4GLD:MIL:EUR"]


def test_isin_alone_still_takes_the_first_hit():
    found, _ = _resolve({ISIN: _LISTINGS}, isin=ISIN)
    assert found == "4GLD:GER:EUR"


def test_symbol_alone_is_unchanged():
    """The symbol search's hits are not filtered by the symbol"""
    results = {"4GLD": [_sec("4GLD:GER:EUR"), _sec("4GLD:LSE:GBX")]}
    found, queries = _resolve(results, symbol="4GLD")
    assert found == "4GLD:GER:EUR"
    assert queries == ["4GLD"]
