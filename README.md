# py-ftmarkets

Financial Times (markets.ft.com) data source for Python. Provides a high-level API and CLI to search for securities, fetch historical data, and validate prices.

## Installation

```bash
uv add py-ftmarkets
# or
pip install py-ftmarkets
```

## CLI Usage

The package provides a CLI tool named `ftmarkets`.

### Lookup a Ticker

Resolve an ISIN or Symbol to the Financial Times ticker format (e.g., `AAPL:NSQ`).

```bash
# Basic lookup by ISIN
ftmarkets lookup --isin DE000A0S9GB0

# Lookup with price and date validation (Returns 1 best matching ticker)
ftmarkets lookup --isin DE000A0S9GB0 --price 117.81 --date 2025-12-12 --limit 1

# Lookup with filters (currency, country, asset-class)
ftmarkets lookup --isin DE000A0S9GB0 --currency EUR --country DE --asset-class ETF

# Return all matching results in JSON format
ftmarkets lookup --isin DE000A0S9GB0 --limit 0 --format json
```

### Fetch History and Validate

Fetch historical data for a resolved ticker and optionally validate a trade price on a specific date.

```bash
# Fetch 1 month of history for an ISIN
ftmarkets history --isin DE000A0S9GB0

# Fetch 1 year of history and validate a price
ftmarkets history --isin DE000A0S9GB0 --period 1y --price 120.50 --date 2025-01-15
```

## Library Usage

`py-ftmarkets` implements the `DataSource` interface from `pydantic-market-data`.

```python
from ftmarkets.api import FTDataSource
from pydantic_market_data.models import SecurityCriteria

source = FTDataSource()

# Resolve a security
criteria = SecurityCriteria(isin="DE000A0S9GB0")
symbol = source.resolve(criteria)
print(f"Ticker: {symbol.ticker}")

# Fetch history
history = source.history(symbol.ticker, period="1mo")
df = history.to_pandas()
print(df.tail())

# Validate price
is_valid = source.validate(symbol.ticker, target_date="2025-01-15", target_price=120.50)
print(f"Price valid: {is_valid}")
```

## Features

- **Robust Resolution**: Searches by ISIN, Symbol, or Description.
- **Smart Mapping**: Prioritizes results based on preferred exchanges and currency.
- **Price Validation**: Verifies if a security traded within a range or near a specific price on a given date.
- **Pandas Integration**: Historical data is easily convertible to Pandas DataFrames.
- **Modern Python**: Built with Pydantic v2 and async-ready architecture (though currently synchronous).
