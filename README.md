# py-ftmarkets

Financial Times (markets.ft.com) data source for Python. Provides a high-level API and CLI to search for securities, fetch historical data, and validate prices.

## Installation

```bash
uv add py-ftmarkets
# or
pip install py-ftmarkets
```

## CLI Usage

The `ftmarkets` CLI is built on [treaty](https://pypi.org/project/treaty/) and needs Python 3.14 and the `cli` extra. The library itself still supports Python 3.10 and later; on an older Python, or without the extra, `ftmarkets` exits 1 and says how to install it.

```bash
uv tool install --python 3.14 "py-ftmarkets[cli]"
# or, in a Python 3.14 environment
pip install "py-ftmarkets[cli]"
```

`--format` takes `plain` (the default on a terminal), `json` (the full response envelope with `ok`, `data`, `error`, `warnings`, and `meta`; the default when piped), `jsonl`, `ndjson`, and `tsv`. `-v` and `-vv` show info and debug logs on stderr. `ftmarkets <command> --help` lists the flags and `--schema` the input and output schemas.

Exit codes (`error.code` in the JSON envelope says which case it is; `error.retryable` whether the same command may succeed later):

| Exit | Name | Retryable | When |
|---|---|---|---|
| `0` | `SUCCESS` | | |
| `2` | `ARG_ERROR` | no | Invalid arguments; nothing ran |
| `4` | `PRECONDITION` | no | `TLS_FAILED`: the TLS connection to FT failed; point `REQUESTS_CA_BUNDLE` or `SSL_CERT_FILE` at the right CA bundle |
| `5` | `NOT_FOUND` | no | Security or history not found |
| `11` | `RATE_LIMITED` | yes | FT answered 429; wait `error.retry_after_ms` (from `Retry-After`, else 30 s) |
| `12` | `UNAVAILABLE` | yes | `CONNECTION_FAILED` (FT unreachable), `UPSTREAM_TIMEOUT` (no answer in time), or `UPSTREAM_UNAVAILABLE` (FT answered 5xx); retry with back-off |
| `79` | `PRICE_MISMATCH` | no | The price check failed (`history --price`) |
| `80` | `UPSTREAM_CHANGED` | no | `FT_PAGE_CHANGED`: FT's page or chart data changed shape; `UPSTREAM_REJECTED`: FT refused the request with a 4xx other than 429. Please [report it](https://github.com/romamo/py-ftmarkets/issues) with the command and the symbol |

Any other failure is a bug in ftmarkets and exits `1` (`HANDLER_CRASHED`).

### Lookup a Security

Resolve an ISIN or Symbol to the Financial Times symbol format (e.g., `AAPL:NSQ`). Results keep FT's relevance order. `--limit` defaults to 1 (the best match) and `--limit 0` returns every match; when more matches exist, `meta.pagination.next_cursor` (or the stderr hint in text formats) is the `--cursor` for the next page.

`--asset-class` takes one of `equity`, `fixed_income`, `cash`, `commodity`, `real_estate`, `fx`, `crypto`, `derivative`, `alternative`, or `index`; `--security-type` matches FT's own category (`ETF`, `Fund`, `Equity`, `Index`) in any case. `--date` is `YYYY-MM-DD`, `YYYY/MM/DD`, or `YYYYMMDD`.

```bash
# Basic lookup by ISIN
ftmarkets lookup --isin DE000A0S9GB0

# The three best matches, then the next three
ftmarkets lookup --isin US0378331005 --limit 3 --format json
ftmarkets lookup --isin US0378331005 --limit 3 --cursor <meta.pagination.next_cursor>

# Keep only matches that traded near a price on a date (--price needs --date)
ftmarkets lookup --isin DE000A0S9GB0 --price 117.81 --date 2025-12-12

# Lookup with filters (currency, country, FT security type, exchange)
ftmarkets lookup --isin DE000A0S9GB0 --currency EUR --country DE --security-type ETF

# Only equity listings (asset class)
ftmarkets lookup --isin US0378331005 --asset-class equity --limit 0

# Every match as a JSON envelope; the securities are in .data
ftmarkets lookup --isin DE000A0S9GB0 --limit 0 --format json

# Every match as a table
ftmarkets lookup --isin DE000A0S9GB0 --limit 0 --format tsv
```

### Fetch History and Validate

Fetch historical data for a resolved security and optionally validate a trade price on a specific date. The result holds the resolved `security`, its `history` (candles in ascending date order), and `validated` (`true` when the price matched, `null` without `--price`).

```bash
# Fetch 1 month of history for an ISIN
ftmarkets history --isin DE000A0S9GB0

# Fetch 1 year of history and validate a price; exits 79 (PRICE_MISMATCH) when it does not match
ftmarkets history --isin DE000A0S9GB0 --period 1y --price 120.50 --date 2025-01-15 --format json
```

## Library Usage

`py-ftmarkets` implements the `DataSource` interface from `pydantic-market-data`.

```python
from datetime import date

from pydantic_market_data.models import HistoryPeriod, SecurityQuery

from ftmarkets.api import FTDataSource

source = FTDataSource()

# Resolve a security (None when FT has no match)
security = source.resolve(SecurityQuery(isin="DE000A0S9GB0"))
assert security is not None
print(f"Symbol: {security.symbol}")

# Fetch history
history = source.history(security.symbol, period=HistoryPeriod.MO1)
print(history.to_pandas().tail())

# Validate a price against the nearest trading day up to 5 days away: True when the price
# is within that day's range, or within 10% of its close; False when FT has no trading
# day that close; raises PriceVerificationError when the day exists but the price misses
is_valid = source.validate(security.symbol, target_date=date(2025, 1, 15), target_price=83.50)
print(f"Price valid: {is_valid}")
```

## Features

- **Robust Resolution**: Searches by ISIN, Symbol, or Description
- **Price Validation**: Verifies if a security traded within a day's range or near a specific price on a given date
- **Pandas Integration**: Historical data is easily convertible to Pandas DataFrames
- **Modern Python**: Built with Pydantic v2, strictly typed
