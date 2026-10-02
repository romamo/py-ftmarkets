# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- **`FTClient(proxies=..., verify=...)`**: a `requests`-style proxy mapping (replacing the proxy environment variables when given, `{}` for a direct connection) and a CA bundle path; `FTClient()` and the shared `client` behave as before (#11)
- **`live` pytest marker**: the tests that hit markets.ft.com are deselected by default (`addopts = "-m 'not live'"`); run them with `uv run pytest -m live` or the manual `Live` workflow (#12)
- **README example test**: the README's library example runs in the default suite against a fake scraper, and unchanged under `live` (#12)

### Changed
- **BREAKING: `FTClient.get`/`FTClient.post`** take only a `/`-relative path on markets.ft.com and raise `ValueError` on anything else, such as an absolute URL, which `get` used to fetch as is (#11)
- **`ftmarkets.cli_app.create_app`** takes a source factory, `Callable[[NetworkSettings], DataSource]`, called once per run with `ctx.network`, instead of a `DataSource`; `ft_source` builds the live FT source (#11)
- **Dependency**: The `cli` extra pins `treaty==1.0.0rc19` (#11)
- **CI**: `publish.yml` runs the unit tests on Python 3.10 and `ftmarkets --version` from the built wheel with the `cli` extra on Python 3.14 before `uv publish`; `ci.yml` uses `astral-sh/setup-uv@v7` like `publish.yml` (#12)
- **Dependency**: Bumped `pydantic-market-data` to `>=0.7.0`
- **BREAKING: `SecurityQuery.price_on` dates**: `pydantic-market-data` 0.7.0 makes `FlexibleDate` accept only `YYYY-MM-DD`, `YYYY/MM/DD`, or `YYYYMMDD` strings (or `date`/`datetime` objects) and reject impossible dates, so a `SecurityQuery` (re-exported from `ftmarkets.api`) or `PriceOnDate` built from any other date string, such as `15/01/2025`, now raises `ValidationError` before it reaches `FTDataSource.resolve()`
- **Search results set `isin` only from a valid ISIN query**: rows of FT's search-results table carry the query as `isin` only when it is a valid ISIN (format and checksum); the other tearsheet links on the page (best matches, which may be unrelated) no longer get it. An exact-match tearsheet takes the ISIN printed on the page, else the query when it is a valid ISIN (#7)
- **`ftmarkets.extract.schemas.Isin` removed**: the scraper uses `pydantic-market-data`'s `ISIN` value object instead (#7)
- **BREAKING: `FTDataSource.resolve()` no longer swallows errors**: during a price check only `PriceVerificationError` moves on to the next candidate; a `ScraperError`, an HTTP or connection error, or any other exception now propagates instead of turning into `None`, so the CLI's `history --price` reports the failure rather than NOT_FOUND or PRICE_MISMATCH (#9)
- **BREAKING: `FTDataSource.resolve()` filters on `SecurityQuery.asset_class` for every `AssetClass`**: it keeps candidates whose `asset_class` equals the query's. Before, `fixed_income`, `cash`, `commodity`, `real_estate`, `fx`, `crypto`, `derivative`, and `alternative` returned `None` without searching the candidates; `equity` and `index` behave as before (#9)
- **BREAKING: `FTDataSource.resolve()` filters on `SecurityQuery.exchange`**: it keeps candidates whose exchange contains the query's, case-insensitively (as `lookup --exchange` does), and drops candidates with no exchange; it used to ignore the field, so `history --exchange` now picks that listing (#9)
- **BREAKING: `FTDataSource.validate()` and `resolve()` reject a target price of 0 or less** with `ValueError` before fetching anything; `pydantic-market-data`'s `Price` accepts any float (#9)
- **`FTDataSource.history(period=HistoryPeriod.D1)`** fetches 5 calendar days and returns only the latest candle, so it has a candle on a Monday or after a holiday (#9)
- **`FTDataSource`** takes an optional `today` callable (default `date.today`) that `history(period=HistoryPeriod.YTD)` counts from (#9)
- **BREAKING: CLI `lookup --price`** no longer treats a scraper or HTTP error during a candidate's price check as "no match": only a failed price check skips the candidate, and any other error stops the command with a treaty error (`HANDLER_CRASHED`, exit 1) instead of answering `NOT_FOUND` or a shorter list (#10)
- **BREAKING: `Scraper.get_history()` raises `ScraperError` on a malformed chart response**: dates without a price element, a missing Open/High/Low/Close series, any series whose length differs from the dates, values without dates, and a body that is not JSON or does not fit `ChartResponse` (which used to raise `ValidationError` or `JSONDecodeError`). It used to return an empty `History` or pad the candles with `None`. A response with an empty `Dates` list is "no data" and still gives an empty `History` (the CLI's `NOT_FOUND`); a missing volume element still leaves `volume` empty (#10)

### Fixed
- **`ftmarkets.__version__`** comes from the installed package metadata instead of a hard-coded `"0.2.0"` (#12)
- **README library example**: imports `SecurityQuery` (not the removed `SecurityCriteria`), passes `target_date` as a `date`, and no longer advertises preferred-exchange mapping or an async-ready architecture, neither of which exists (#12)
- **Package metadata**: `[project.urls]` point to `romamo/py-ftmarkets`; `pycountry`, `pydantic-extra-types`, and `urllib3`, imported directly, are declared dependencies; the sdist no longer ships `CLAUDE.md`, `GEMINI.md`, `docs/sessions/`, or `.claude/` (#12)
- **`search()` crashed on 12-character non-ISIN queries** such as `AMAZONCOMINC`, which were taken for ISINs and failed `Security` validation; they now leave `isin` unset (#7)
- **Search `exchange` included FT's badge text**: `London Stock ExchangePrimary` is now `London Stock Exchange` (#7)
- **Search results section headers and tearsheet names with child elements** no longer crash parsing (#7)
- **A tearsheet stating an invalid ISIN** now raises `ScraperError` naming the value instead of a `Security` `ValidationError` (#7)
- **`FTDataSource.history(period=HistoryPeriod.YTD)`** fetched 30 days; it now fetches the days since 1 January. An unmapped period raises `KeyError` instead of falling back to 30 days (#9)
- **CLI `--date`**: still accepts the same three formats and still exits 2 on anything else, but the check is now `pydantic-market-data`'s `FlexibleDate` instead of the CLI's own; the error message reads `Invalid date: '15/01/2025'; expected YYYY-MM-DD, YYYY/MM/DD or YYYYMMDD`
- **Query encoding**: `Scraper.get_xid` sends the symbol as an encoded `s` query parameter instead of pasting it into the URL, so a symbol containing `&`, `=`, or `#` no longer adds or cuts parameters (#11)
- **CLI `--proxy`, `--no-proxy`, and CA bundle**: treaty's network settings (`--proxy`, `--no-proxy`, `HTTP(S)_PROXY`, `NO_PROXY`, `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`) now reach the `requests.Session` the CLI talks to FT with; before, the flags were advertised but had no effect (#11)
- **XID regex fallback**: `Scraper.get_xid` also finds a quoted JSON key, `"xid": "123"` or `"xid":123`, when the tearsheet's `data-mod-config` does not hold it; the fallback matched only `xid:`, `xid=`, and `&quot;xid&quot;` (#11)

### Removed
- **BREAKING: `ftmarkets.utils`** and its `parse_date`, which nothing in the package used, returned `None` on bad input, and read `01/02/2025` day first; use `pydantic-market-data`'s `parse_date` or `FlexibleDate` (#12)

## [0.7.0] - 2026-10-02

### Added
- **CLI `lookup --security-type`**: matches FT's security type (`ETF`, `Fund`, `Equity`, `Index`) case-insensitively; it takes over the FT-category half of the old `--asset-class` (#4)
- **CLI `lookup --cursor`**: when more matches exist than `--limit`, `meta.pagination.next_cursor` pages through them in FT's relevance order. With `--price`, a page validates only as many candidates as it needs, and the next page resumes the scan where it stopped, so the last page can be empty (#4)

### Changed
- **BREAKING: CLI `lookup --asset-class`** takes only `pydantic-market-data`'s `AssetClass` values in lower case (`equity`, `fixed_income`, `cash`, `commodity`, `real_estate`, `fx`, `crypto`, `derivative`, `alternative`, `index`) and matches the security's asset class only; any other value (`ETF`, `Fund`, `Equity`) exits 2. Use `--security-type ETF` for FT's categories (#4)
- **BREAKING: CLI `--date`** accepts `YYYY-MM-DD`, `YYYY/MM/DD`, or `YYYYMMDD`; `DD/MM/YYYY` and other day- or month-first dates now exit 2 instead of being read day first (#4)
- **CLI `lookup --limit`** is treaty's framework flag: it still defaults to 1 and `0` still returns every match, and the response's `meta.pagination` now carries `total`, `has_more`, and `next_cursor` (#4)
- **Dependency**: Bumped `pydantic-market-data` to `>=0.6.1`; the CLI uses its `FlexibleDate` and `AssetClass` argument types (#4)
- **Dependency**: The `cli` extra pins `treaty==1.0.0rc11` (#4)

## [0.6.0] - 2026-10-01

### Changed
- **BREAKING: the CLI is built on treaty and needs Python 3.14**: install it with `pip install "py-ftmarkets[cli]"` on Python 3.14 (the new `cli` extra pins `treaty==1.0.0rc10`). The library still supports Python 3.10; on an older Python, or without the extra, `ftmarkets` exits 1 naming the running Python and the install command (#2)
- **BREAKING: CLI output**: `--format json` writes treaty's response envelope (`ok`, `data`, `error`, `warnings`, `meta`) instead of a bare list; `lookup` returns a list of securities (each marked `_source: external`, `_trusted: false`, with an `UNTRUSTED_CONTENT` warning), and `history` returns `{security, history, validated}` instead of printing "Resolved to:", a pandas table, and "VALIDATION PASSED". The default format is `plain` on a terminal and `json` when piped; `jsonl`, `ndjson`, and `tsv` are also available (#2)
- **BREAKING: CLI exit codes**: invalid arguments exit 2 before anything runs (no `--isin`/`--symbol`/`--desc`, `--price` without `--date`, a malformed `--date`, an unknown `--period`); a security or history not found exits 5 (`NOT_FOUND`) instead of 1; a failed price check in `history` exits 79 (`PRICE_MISMATCH`) instead of 1 (#2)
- **CLI `lookup --asset-class`** matches the security's asset class (`equity`, `index`, ...) or FT's security type (`ETF`, `Fund`, ...), case-insensitively; it matched nothing since 0.5.1 made `asset_class` an enum (#2)
- **Dependency**: Bumped `pydantic-market-data` to `>=0.5.0` and use its `SecurityQueryArgs`/`HistoryQueryArgs` (#2)

### Removed
- **BREAKING: CLI `--format xml`** and the `text` format; use `plain`, `json`, `jsonl`, `ndjson`, or `tsv` (#2)
- **BREAKING: CLI `--v`/`--vv` and `--schema` from `pydantic-market-data`'s `GlobalArgs`**: treaty's `-v`/`--verbose`, `-vv`/`--debug`, and `--schema` replace them (#2)
- **`ftmarkets.commands`** (`LookupCommand`, `HistoryCommand`) and `ftmarkets.cli.setup_logging`/`AppCLI`; the CLI lives in `ftmarkets.cli_app` (#2)
- **Dependency**: `pydantic-settings` is no longer a direct dependency (#2)

## [0.5.1] - 2026-09-30

### Changed
- **`Security.asset_class` is an `AssetClass` enum**: search and tearsheet results map FT's categories to `pydantic-market-data`'s `AssetClass` (ETFs and equities to `EQUITY`, indices to `INDEX`, funds to `None`); the FT category name (`"ETF"`, `"Equity"`, `"Fund"`, `"Index"`) moves to `security_type`
- **Dependency**: Bumped `pydantic-market-data` to `>=0.4.0`

### Fixed
- **Price validation with `pydantic-market-data` 0.4**: `resolve()` read `SecurityQuery.price_on` as a single point, but 0.4 makes it a list, so every price-validated resolve failed. A candidate now has to match every price point, from one history fetch starting at the earliest date

## [0.5.0] - 2026-05-08

### Added
- **`price_tolerance` parameter**: `FTDataSource.validate()` now accepts a configurable `price_tolerance` float (default `0.10`) instead of a hardcoded 5% threshold.

### Changed
- **Dependency**: Bumped `pydantic-market-data` to `>=0.3.2`.

## [0.4.0] - 2026-04-23

### Changed
- **Dependency**: Bumped `pydantic-market-data` to `>=0.3.0`.
- **Breaking**: `SecurityCriteria` renamed to `SecurityQuery` to align with `pydantic-market-data` 0.3.0.
- **Breaking**: `target_price` and `target_date` fields consolidated into `price_on: PriceOnDate` on `SecurityQuery`.

### Added
- **FIGI support**: `FTDataSource.resolve()` now searches by FIGI first (most specific) before falling back to ISIN → symbol → description.

## [0.3.0] - 2026-04-14

### Changed
- **Model Renaming**: Aligned with `pydantic-market-data` core models.
    - `Ticker` renamed to `Symbol`.
    - `Symbol` renamed to `Security`.
- **API Alignment**: Updated `FTDataSource` methods to use new model names.

## [0.2.0] - 2026-04-03

### Added
- **Asset Class Filtering**: Added strict filtering for `STOCK`, `EQUITY`, `ETF`, `INDEX`, and `FUND` in `FTDataSource.search`.

## [0.1.9] - 2026-03-01

### Internal
- Version bump in `pyproject.toml`.

## [0.1.1] - 2026-02-09

### Fixed
- Remove local dependency `pydantic-market-data` from `pyproject.toml` to fix PyPI installation.

## [0.1.0] - 2026-02-06

### Added
- Initial release of `py-ftmarkets`.
- **Search API**: Resolve ISINs, symbols, and names to Financial Times tickers.
- **History API**: Fetch historical OHLCV data with flexible periods.
- **Price Validation**: Verify trade prices against historical data within the `DataSource` interface.
- **Enhanced Lookup CLI**: New `--isin`, `--symbol`, `--price`, `--date`, `--currency`, `--country`, and `--asset-class` filters.
- **Multi-format Output**: Support for `text`, `json`, and `xml` in matching results.
- **Early Exit Optimization**: Speed up lookups when using `--limit` with price validation.
- **OSS Best Practices**: MIT license, CI/CD workflows, and full `src`-layout.
- **Market Data Integration**: Built on `pydantic-market-data` shared models.
