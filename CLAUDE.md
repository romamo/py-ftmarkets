# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                        # install dependencies
uv run pytest                  # run all tests
uv run pytest tests/smoke_test.py  # run a single test file
uv run ruff check .            # lint
uv run ruff format .           # format
uv run mypy src                # type check
uv build                       # build package (hatchling)
uv run ftmarkets --help        # run CLI
```

## Architecture

`py-ftmarkets` is a `DataSource` implementation for the `pydantic-market-data` ecosystem. The public surface is `FTDataSource` in `src/ftmarkets/api.py`, which implements the `DataSource` interface (resolve, search, history, validate, get_price).

**Layer stack (bottom-up):**

1. **`FTClient`** (`client.py`) — stateless `requests.Session` wrapper with browser-mimicking headers and retry logic. A module-level singleton `client` is shared by default.

2. **`Scraper`** (`extract/scraper.py`) — all HTML scraping and API calls. Two main paths:
   - `search()`: GETs `/data/search`, parses HTML with lxml. Handles both a standard search-results page and a direct tearsheet redirect (exact match).
   - `get_history()`: calls `get_xid()` to extract FT's internal numeric XID from the tearsheet page (tries `data-mod-config` JSON, falls back to regex), then POSTs to `/data/chartapi/series` using the Pydantic models in `extract/schemas.py`.

3. **`FTDataSource`** (`api.py`) — orchestrates Scraper calls, applies filters (asset class, currency), and validates price against OHLCV history using a ±5-day window and a 5% close tolerance.

4. **CLI** (`cli.py`, `cli_app.py`): a treaty app (Python 3.14+, `cli` extra) with `lookup` and `history` commands. Entry point: `ftmarkets` → `ftmarkets.cli:main`, which checks the Python version and that treaty imports before loading `cli_app`. `create_app(source_factory)` takes a `Callable[[NetworkSettings], DataSource]` called once per run with `ctx.network`; the live app uses `ft_source`, which builds `FTClient` from treaty's proxy and CA bundle settings, and `tests/unit/test_cli.py` passes a factory returning a fake and runs commands through `App.call`. Run the CLI and its tests with `uv run -p 3.14 --extra cli ...`; audit with `uv run -p 3.14 --extra cli treaty audit ftmarkets.cli_app:app --strict`.

**Key internal types** (`extract/schemas.py`): `Xid` (FT's internal numeric ID), `ChartRequest`/`ChartResponse` (strict Pydantic models for `/data/chartapi/series`). `Symbol` is imported from `pydantic_market_data.models`.

**Scraper is the fragile part.** When FT changes its website structure, `_parse_search_results`, `_parse_tearsheet_as_search_result`, and `get_xid` are the methods to update. Verify with integration tests against live `markets.ft.com`.

## Testing

Tests live in `tests/`. Currently only `tests/smoke_test.py` exists (basic import/instantiation check). Integration tests that hit live FT endpoints should be used carefully to avoid rate limiting.
