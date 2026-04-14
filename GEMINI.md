# Project Context: `py-ftmarkets`

`py-ftmarkets` is a Python library and CLI designed to fetch and validate financial data from the [Financial Times (markets.ft.com)](https://markets.ft.com). It serves as a `DataSource` implementation for the `pydantic-market-data` ecosystem.

## Project Overview

-   **Purpose:** Provide a high-level API and CLI to search for securities (ISIN, Symbol, Description), fetch historical OHLCV data, and validate trade prices.
-   **Core Technologies:**
    -   **Python 3.10+** (modern type hints and features).
    -   **Pydantic v2 & Pydantic-Settings:** Data modeling, validation, and CLI configuration.
    -   **Requests & LXML:** HTTP communication and robust HTML scraping.
    -   **Pandas:** Handling and exporting historical data series.
    -   **UV & Hatchling:** Modern package management and build system.
-   **Architecture:**
    -   `FTClient`: Stateless wrapper for FT's website with retries and browser-mimicking headers.
    -   `Scraper`: Strict data extraction logic for FT's search results and internal Chart API.
    -   `FTDataSource`: Implementation of the `DataSource` interface from `pydantic-market-data`.
    -   `AppCLI`: Pydantic-Settings-based CLI tool providing `lookup` and `history` commands.

## Getting Started

### Installation & Setup

This project uses `uv` for dependency management.

```bash
# Install dependencies
uv sync

# Run the CLI
uv run ftmarkets --help
```

### Building and Running

-   **Build:** `uv build` (uses Hatchling).
-   **Test:** `uv run pytest` (runs unit, integration, and smoke tests).
-   **Linting:** `uv run ruff check .`
-   **Formatting:** `uv run ruff format .`
-   **Type Checking:** `uv run mypy src`

## Development Conventions

-   **Code Style:** Strictly follow PEP 8; enforced via `ruff`.
-   **Type Safety:** All public APIs and internal logic must have comprehensive type hints. Verified with `mypy`.
-   **Data Models:** Use Pydantic models for all data exchange (e.g., `Symbol`, `OHLCV`, `History`).
-   **Scraping Integrity:** The `Scraper` is the most critical part. When FT changes its website, update `src/ftmarkets/extract/scraper.py` and verify with integration tests.
-   **Interfaces:** Adhere to the `DataSource` interface from `pydantic-market-data` to ensure compatibility with downstream tools.
-   **Testing Strategy:**
    -   `tests/unit`: Mocked tests for core logic.
    -   `tests/integration`: Live tests against markets.ft.com (run with caution to avoid rate limiting).
    -   `tests/smoke_test.py`: Fast verification of basic installation and CLI.

## Key Files

-   `src/ftmarkets/api.py`: Main `FTDataSource` implementation.
-   `src/ftmarkets/client.py`: `FTClient` for HTTP requests.
-   `src/ftmarkets/extract/scraper.py`: Core scraping and parsing logic.
-   `src/ftmarkets/cli.py`: CLI entry point and configuration.
-   `src/ftmarkets/extract/schemas.py`: Internal Pydantic models for FT's Chart API.
-   `pyproject.toml`: Project metadata, dependencies, and tool configurations.
