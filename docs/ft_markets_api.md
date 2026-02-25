# FT Markets API Documentation

This document details the reverse-engineered API for [markets.ft.com](https://markets.ft.com), covering the process to search for securities, extract internal identifiers (`xid`), and fetch historical/chart data.

## Overview

The data extraction workflow primarily consists of three steps:
1.  **Search**: Find the security (Equity, ETF, or Index) to get its page URL or ticker.
2.  **Discovery**: Extract the internal numeric ID (`xid`) from the security's summary/tearsheet page.
3.  **Data Fetch**: Use the `xid` to query the `chartapi` for historical prices and quotes.

---

## 1. Search

**Endpoint:** `GET https://markets.ft.com/data/searchapi/searchsecurities`

### Request
*   **Query Parameters:**
    *   `query`: The search term (Symbol, ISIN, or Name).
*   **Headers:** Standard browser headers.

### Example
**Request:** `GET https://markets.ft.com/data/searchapi/searchsecurities?query=AAPL`

**Response (JSON snippet):**
```json
{
  "data": {
    "symbol": {
      "ticker": "AAPL:NSQ",
      "name": "Apple Inc",
      "exchange": "Nasdaq",
      "xid": "36276" 
    }
  }
}
```
*Note: The `xid` might be present here, but it is most reliably found on the security's HTML page.*

---

## 2. Internal ID (`xid`) Discovery

The `chartapi` requires a numeric `xid`, which differs from the public ticker (e.g., `AAPL:NSQ`).

**Location:**
The `xid` is embedded in the HTML of the security's "tearsheet" or "summary" page, typically within a `data-mod-config` attribute of a section or div element.

**Pattern:**
Look for a JSON object in `data-mod-config` containing the key `xid`.

### URL Patterns
*   **Equity:** `https://markets.ft.com/data/equities/tearsheet/summary?s=AAPL:NSQ`
*   **ETF:** `https://markets.ft.com/data/etfs/tearsheet/summary?s=4GLD:GER:EUR`
*   **Index:** `https://markets.ft.com/data/indices/tearsheet/summary?s=INX:IOM`

### Extraction Example
**HTML Source:**
```html
<section class="module-wrapper" data-mod-config='{"xid":"9894153","symbol":"4GLD:GER:EUR",...}'>
```
**Extracted `xid`:** `9894153`

### Validated XIDs
| Type | Ticker | Name | Validated XID |
| :--- | :--- | :--- | :--- |
| **Equity** | `AAPL:NSQ` | Apple Inc | `36276` |
| **ETF** | `4GLD:GER:EUR` | Xetra-Gold (DE000A0S9GB0) | `9894153` |
| **Index** | `INX:IOM` | S&P 500 | `575769` |

---

## 3. Historical & Chart Data

**Endpoint:** `POST https://markets.ft.com/data/chartapi/series`

This endpoint provides OHLC (Open, High, Low, Close) and Volume data. It is consistent across all asset classes.

### Request

**Headers:**
*   `Content-Type`: `application/json`
*   **Cookies**: Session cookies are required. Specifically, identifiers like `spoor-id` and `FTConsent` appear necessary, implying that a session should be established (e.g., via a standard `requests.Session` that visits the page first).

**Payload:**
```json
{
  "days": 180,            // Number of days of history
  "dataPeriod": "Day",    // "Day", "Week", "Month"
  "dataInterval": 1,
  "realtime": false,
  "yFormat": "0.###",
  "timeServiceFormat": "JSON",
  "returnDateType": "ISO8601",
  "elements": [
    {
      "Type": "price",
      "Symbol": "36276",  // <--- The numeric XID goes here
      "OverlayIndicators": [],
      "Params": {}
    },
    {
      "Type": "volume",   // Optional: Request volume data
      "Symbol": "36276",
      "OverlayIndicators": [],
      "Params": {}
    }
  ]
}
```

### Response

The response contains aligned arrays of dates and values.

**JSON Structure:**
```json
{
  "Dates": [
    "2025-08-25T00:00:00",
    "2025-08-26T00:00:00",
    // ... more dates
  ],
  "Elements": [
    {
      "Type": "price",
      "Symbol": "36276",
      "ComponentSeries": [
        {
          "Type": "Open",
          "Values": [226.17, 227.0, ...] // Array matching Dates length
        },
        {
          "Type": "High",
          "Values": [229.09, 228.5, ...]
        },
        {
          "Type": "Low",
          "Values": [225.41, 226.0, ...]
        },
        {
          "Type": "Close",
          "Values": [227.76, 228.1, ...]
        }
      ]
    },
    {
      "Type": "volume",
      "Symbol": "36276",
      "ComponentSeries": [
        {
          "Type": "Volume",
          "Values": [15000000, 14500000, ...]
        }
      ]
    }
  ]
}
```

### Notes
*   **Authentication:** While the API is technically public, it enforces headers and cookies associated with a valid browser session. Accessing it programmatically requires mimicking these headers or maintaining a session.
*   **Rate Limiting:** Standard polite crawling rules apply.
