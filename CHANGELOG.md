# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Changed
- **Dependency**: Bumped `pydantic-market-data` to `>=0.7.0`
- **BREAKING: `SecurityQuery.price_on` dates**: `pydantic-market-data` 0.7.0 makes `FlexibleDate` accept only `YYYY-MM-DD`, `YYYY/MM/DD`, or `YYYYMMDD` strings (or `date`/`datetime` objects) and reject impossible dates, so a `SecurityQuery` (re-exported from `ftmarkets.api`) or `PriceOnDate` built from any other date string, such as `15/01/2025`, now raises `ValidationError` before it reaches `FTDataSource.resolve()`. `ftmarkets.utils.parse_date` is unchanged
- **Search results set `isin` only from a valid ISIN query**: rows of FT's search-results table carry the query as `isin` only when it is a valid ISIN (format and checksum); the other tearsheet links on the page (best matches, which may be unrelated) no longer get it. An exact-match tearsheet takes the ISIN printed on the page, else the query when it is a valid ISIN (#7)
- **`ftmarkets.extract.schemas.Isin` removed**: the scraper uses `pydantic-market-data`'s `ISIN` value object instead (#7)

### Fixed
- **`search()` crashed on 12-character non-ISIN queries** such as `AMAZONCOMINC`, which were taken for ISINs and failed `Security` validation; they now leave `isin` unset (#7)
- **Search `exchange` included FT's badge text**: `London Stock ExchangePrimary` is now `London Stock Exchange` (#7)
- **Search results section headers and tearsheet names with child elements** no longer crash parsing (#7)
- **A tearsheet stating an invalid ISIN** now raises `ScraperError` naming the value instead of a `Security` `ValidationError` (#7)
- **CLI `--date`**: still accepts the same three formats and still exits 2 on anything else, but the check is now `pydantic-market-data`'s `FlexibleDate` instead of the CLI's own; the error message reads `Invalid date: '15/01/2025'; expected YYYY-MM-DD, YYYY/MM/DD or YYYYMMDD`

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
