# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                        # install dependencies
uv run pytest                  # run the offline tests (live ones are deselected)
uv run pytest -m live          # run only the tests that hit markets.ft.com
uv run pytest tests/unit/test_api.py  # run a single test file
uv run ruff check .            # lint
uv run ruff format .           # format
uv run mypy src                # type check
uv build                       # build package (hatchling)
uv run ftmarkets --help        # run CLI
```

## Architecture

`py-ftmarkets` is a `DataSource` implementation for the `pydantic-market-data` ecosystem. The public surface is `FTDataSource` in `src/ftmarkets/api.py`, which implements the `DataSource` interface (resolve, search, history, validate, get_price).

**Layer stack (bottom-up):**

1. **`FTClient`** (`client.py`) — stateless `requests.Session` wrapper with browser-mimicking headers and retry logic. It takes only `/`-relative paths (anything else raises `ValueError`) and optional `proxies`/`verify`. A module-level singleton `client` is shared by default.

2. **`Scraper`** (`extract/scraper.py`) — all HTML scraping and API calls. Two main paths:
   - `search()`: GETs `/data/search`, parses HTML with lxml. Handles both a standard search-results page and a direct tearsheet redirect (exact match).
   - `get_history()`: calls `get_xid()` to extract FT's internal numeric XID from the tearsheet page (tries `data-mod-config` JSON, falls back to regex), then POSTs to `/data/chartapi/series` using the Pydantic models in `extract/schemas.py`.

3. **`FTDataSource`** (`api.py`) — orchestrates Scraper calls; `resolve()` keeps candidates whose `asset_class` equals the query's, whose exchange contains `SecurityQuery.exchange` (case-insensitive; candidates without an exchange are dropped), and whose currency matches, then validates price against OHLCV history using a ±5-day window: a price inside the day's low-high range passes, else one within `price_tolerance` (default 0.10, i.e. 10%) of the close.

4. **CLI** (`cli.py`, `cli_app.py`): a treaty app (Python 3.14+, `cli` extra) with `lookup` and `history` commands. Entry point: `ftmarkets` → `ftmarkets.cli:main`, which checks the Python version and that treaty imports before loading `cli_app`. `create_app(source_factory)` takes a `Callable[[NetworkSettings], DataSource]` that each handler calls once per run with `ctx.network`. The live app is `create_app(ft_source)`: `ft_source` builds `FTDataSource(Scraper(http_client=ft_client(network)))`, and `ft_client` hands `FTClient` the proxy for markets.ft.com (`--proxy`, else the proxy variables with `NO_PROXY` applied, none under `--no-proxy`) and the CA bundle. Both handlers run inside `ft_failures(...)`, the one place FT's failures become declared exit codes (`UNAVAILABLE` 12 and `RATE_LIMITED` 11, retryable; `UPSTREAM_CHANGED` 80 for a `ScraperError` or a 4xx; `PRECONDITION` 4 for TLS); anything else still crashes as `HANDLER_CRASHED`. `tests/unit/test_cli.py` passes `lambda network: fake` and runs commands through `App.call`; `tests/unit/test_cli_network.py` checks the network settings reach the session. Run the CLI and its tests with `uv run -p 3.14 --extra cli ...`; audit with `uv run -p 3.14 --extra cli treaty audit ftmarkets.cli_app:app --strict`.

**Key internal types** (`extract/schemas.py`): `Xid` (FT's internal numeric ID), `ChartRequest`/`ChartResponse` (strict Pydantic models for `/data/chartapi/series`). `Symbol` is imported from `pydantic_market_data.models`.

**Scraper is the fragile part.** When FT changes its website structure, `_parse_search_results`, `_parse_tearsheet_as_search_result`, `_xid_from_tearsheet`, `_tearsheet_currency` (the quote currency, pence as `GBX`, from the tearsheet's `data-mod-config` or its "Price (GBX)" label), and `_tearsheet_listing` (exchange and country from the symbol menu) are the methods to update. Verify with the `live` tests against `markets.ft.com`.

## Testing

- `tests/unit/`: offline tests; the default `uv run pytest` runs them. Fakes are injected through constructors (`Scraper(http_client=...)`, `FTDataSource(scraper_instance=...)`), never patched in. `test_readme.py` runs the README's Python example against a fake scraper
- `tests/unit/test_cli.py`: the treaty CLI through `App.call`; it needs Python 3.14 and the `cli` extra (`uv run -p 3.14 --extra cli pytest`) and is skipped elsewhere
- `tests/integration/test_api_live.py`: hits live markets.ft.com, marked `live` and deselected by `addopts`; run with `uv run pytest -m live`, or the manual `Live` workflow (`.github/workflows/live.yml`). Use sparingly to avoid rate limiting
- `tests/smoke_test.py`: an import check `publish.yml` runs against the built wheel and sdist
