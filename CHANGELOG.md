# Changelog

All notable changes to this project will be documented in this file.

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
