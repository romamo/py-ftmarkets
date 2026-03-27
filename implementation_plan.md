# Implementation Plan - Code Review and Refinement for py-ftmarkets

Review of `py-ftmarkets` codebase focusing on `ftmarkets/api.py`, `ftmarkets/extract/scraper.py`, and CLI commands.

## User Review Required

### Global Rules Check
- **Namespace Pattern**: The codebase uses `Ticker | str` in many places. As per `MEMORY[user_global]`, we should prefer the **Namespace Pattern** (`Ticker.Input`) for better DX and consistency.
- **Fail Fast**: The code generally follows this, but there are some generic `RuntimeError` usages in `api.py` that could be more specific.
- **Primitive Obsession**: Some internal methods (like `_find_nearest_candle`) have untyped or loosely typed arguments.

### Critical Issues
- No critical security or logic bugs found during static analysis.
- Redundant manual country-to-code mapping in `scraper.py` that can be replaced by `pycountry` (already available via `pydantic-market-data`).

### Architecture
- The separation between `FTDataSource` (API level) and `Scraper` (low-level parsing) is good.
- Use of Pydantic VOs is consistent with the `pydantic-market-data` core.

### Ambiguities
- In `FTDataSource.get_price`, if no price is found, a `RuntimeError` is raised. Should we use a more specific exception (e.g., from `pydantic-market-data` if available, or a custom one)?

## Static Analysis & Coverage

### Ruff/Mypy/Bandit
- **Ruff**: 0 issues.
- **Bandit**: 0 issues.
- **Mypy**: 33 errors reported (mainly missing stubs/dependencies in the analysis environment). Manual review shows mostly correct typing, but some namespace improvements are needed.

### Test Coverage
- **Total Project**: 97.28%
- **ftmarkets/api.py**: 98.28%
  - Line 144: `validate` method path for `float` input not covered.
  - Line 159: `_ensure_datetime` path for `None` input not covered.
- **ftmarkets/extract/scraper.py**: 92.0%
  - Missing coverage for several error branches and regex fallbacks in `get_xid`.
  - Some helper methods for country/currency mapping are not fully exercised.

## Proposed Changes

### 1. Refactor `ftmarkets/api.py`
- [ ] Apply **Namespace Pattern**: Replace `Ticker | str`, `Price | float`, `HistoryPeriod | str` with `Ticker.Input`, `Price.Input`, `HistoryPeriod.Input`.
- [ ] Add strict types to internal methods (e.g., `target_date: date` in `_find_nearest_candle`).
- [ ] Improve exception message/type in `get_price` to fail faster and clearer.

### 2. Refactor `ftmarkets/extract/scraper.py`
- [ ] Replace manual `_map_country_to_code` dictionary with `pycountry` lookup (leverage `validate_country_code` logic or direct `pycountry` calls).
- [ ] Simplify `_map_country_to_currency` if possible.
- [ ] Ensure all `Input` types follow the namespace pattern.

### 3. CLI & Utils
- [x] Ensure `lookup.py` and `history.py` use the namespace pattern for arguments.
- [x] (Optional) Add more comprehensive logging for failed resolution in `resolve`.

### 4. Improve Coverage
- [x] Add unit tests for `FTDataSource.validate` with `float` target price.
- [x] Add unit tests for `FTDataSource.get_price` with `None` date.
- [x] Add tests for `Scraper.get_xid` regex fallback and error cases.

## Verification Plan
- [x] Run `uv run pytest --cov=src` to verify coverage improvements.
- [x] Run `uvx mypy src` (with proper environment) to verify type safety.
- [x] Run `uvx ruff check src` to ensure no style regressions.
