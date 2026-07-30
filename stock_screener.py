#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stock screener - reads NSE symbols from a CSV and checks Sales CAGR.

Criteria:
  Sales CAGR >= 15% over the last 2 years
  e.g. if current FY is 2027, check FY24 -> FY25 -> FY26 (3 data points, 2-year window)
"""

from __future__ import annotations

import sys
import os
import csv
import argparse
import html
import re

# Force UTF-8 output on Windows so arrow/rupee characters don't crash
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import yfinance as yf

# Config
_INPUTS_DIR = os.path.join(os.path.dirname(__file__), "Inputs")
INPUT_FILES = {
    "growth-gap": os.path.join(_INPUTS_DIR, "growth-gap-strategy-mp.csv"),
    "nifty-500":  os.path.join(_INPUTS_DIR, "nifty-500.csv"),
    "nifty-500-v2": os.path.join(_INPUTS_DIR, "nifty-500 (2).csv"),
    "nifty-500-copy":  os.path.join(_INPUTS_DIR, "nifty-500-copy.csv"),
}
INPUT_FILE = INPUT_FILES["growth-gap"]   # default
MIN_SALES_CAGR_PCT = 15.0
YEARS = 2                 # 2-year CAGR window
DELAY_BETWEEN_STOCKS = 1  # seconds between batches
CHECKPOINT_EVERY     = 25  # save CSV progress every N stocks
ROCE_CACHE_DAYS      = 90  # reuse cached ROCE within this many days
WORKERS              = 5   # parallel threads (increase for speed, risk more rate-limits)

# Output mode: "csv" | "gsheet" | "both"
OUTPUT_MODE = "csv"

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--output",
        choices=["csv", "gsheet", "both"],
        default=OUTPUT_MODE,
        help="Output destination: csv, gsheet, or both",
    )
    parser.add_argument(
        "--input",
        choices=list(INPUT_FILES.keys()),
        default="growth-gap",
        help="Input stock list: growth-gap or nifty-500",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        metavar="N",
        help="Randomly sample N stocks from the input file (e.g. --sample 100)",
    )
    return parser.parse_args(argv)


def compute_cagr(start: float, end: float, n_years: int) -> float | None:
    """CAGR = (end/start)^(1/n) - 1, returns as percentage."""
    if start <= 0 or end <= 0 or n_years <= 0:
        return None
    return ((end / start) ** (1.0 / n_years) - 1) * 100.0


def _clean_html_text(raw: str) -> str:
    """Strip tags/entities and normalize whitespace from HTML fragments."""
    text = re.sub(r"<[^>]+>", "", raw)
    text = html.unescape(text)
    return " ".join(text.split()).strip()


def _is_recent_fy_series(annual_revenue: dict[str, float]) -> bool:
    """Return True only when annual FY labels include a recent fiscal year."""
    from datetime import date

    fy_years: list[int] = []
    for label in annual_revenue.keys():
        m = re.fullmatch(r"FY(\d{2})", str(label).strip())
        if m:
            fy_years.append(2000 + int(m.group(1)))

    if not fy_years:
        return False

    # Reject stale partial slices like FY14-FY17 for current-period screening.
    return max(fy_years) >= (date.today().year - 2)


def _has_complete_screener_annual(annual_revenue: dict[str, float]) -> bool:
    """Minimum annual data quality needed for strict Screener-only mode."""
    return len(annual_revenue) >= YEARS + 1 and _is_recent_fy_series(annual_revenue)


def _fetch_screener_html(symbol_plain: str, retries: int = 3, timeout: int = 12) -> str | None:
    """Fetch screener company page with small retries for transient network/page issues."""
    import time
    import urllib.request

    url = f"https://www.screener.in/company/{symbol_plain}/consolidated/"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception:
            if attempt < retries:
                time.sleep(0.5 * attempt)
    return None


def _parse_screener_financials(page_html: str) -> tuple[float | None, float | None, dict[str, float]]:
    """Parse ROCE, TTM, and annual revenue from screener HTML only."""
    roce: float | None = None
    ttm: float | None = None
    annual: dict[str, float] = {}

    # ROCE
    m = re.search(r'ROCE[\s\S]{0,300}?<span class="number">([\d.]+)</span>', page_html, re.IGNORECASE)
    if m:
        roce = float(m.group(1))

    # Scope to Profit & Loss section for deterministic table parsing.
    pl = re.search(
        r'<section[^>]*id="profit-loss"[^>]*>(.*?)</section>',
        page_html,
        re.DOTALL | re.IGNORECASE,
    )
    if not pl:
        return roce, ttm, annual

    pl_html = pl.group(1)

    th_values_raw = re.findall(r"<th[^>]*>(.*?)</th>", pl_html, re.DOTALL | re.IGNORECASE)
    th_values = [_clean_html_text(v) for v in th_values_raw]
    th_values = [v for v in th_values if v]

    mar_years = re.findall(r"\bMar\s+(\d{4})\b", " | ".join(th_values))
    fy_labels = [f"FY{y[2:]}" for y in mar_years]
    has_ttm_col = any(v.upper() == "TTM" for v in th_values)

    revenue_row_html = None
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", pl_html, re.DOTALL | re.IGNORECASE):
        first_td = re.search(r'<td[^>]*class="[^"]*text[^"]*"[^>]*>(.*?)</td>', tr, re.DOTALL | re.IGNORECASE)
        if not first_td:
            continue
        label = _clean_html_text(first_td.group(1)).lower()
        if label.startswith("revenue") or label.startswith("sales"):
            revenue_row_html = tr
            break

    if not revenue_row_html:
        return roce, ttm, annual

    numbers = re.findall(r"<td[^>]*>\s*([\d,]+)\s*</td>", revenue_row_html)
    if not numbers:
        return roce, ttm, annual

    annual_vals = numbers
    if has_ttm_col:
        ttm = round(float(numbers[-1].replace(",", "")), 2)
        annual_vals = numbers[:-1]

    relevant_labels = fy_labels[-len(annual_vals):] if fy_labels else []
    for fy, val in zip(relevant_labels, annual_vals):
        annual[fy] = round(float(val.replace(",", "")), 2)

    return roce, ttm, annual


def get_annual_revenue(symbol: str) -> tuple[dict[str, float], str]:
    """
    Returns ({fiscal_year_label: revenue_in_crores}, currency_code) sorted oldest-first.
    Strict mode: uses Screener-derived cache only; no yfinance fallback.
    """
    # --- Use Screener-derived cache only ---
    symbol_plain = symbol.split(".")[0]
    cache = _load_roce_cache()
    entry = cache.get(symbol_plain)
    if entry and entry.get("annual_revenue"):
        from datetime import date
        age = (date.today() - date.fromisoformat(entry["fetched_on"])).days
        annual = entry.get("annual_revenue") or {}
        if age <= ROCE_CACHE_DAYS and _has_complete_screener_annual(annual):
            return dict(sorted(entry["annual_revenue"].items())), "INR"

    raise ValueError(
        f"Screener annual revenue unavailable/incomplete for {symbol}. "
        f"(strict single-source mode)"
    )


def annual_to_quarterly_rate(annual_pct: float) -> float:
    """Convert annual growth % to equivalent quarterly growth %.
    e.g. 15% annual -> (1.15)^(1/4) - 1 = 3.56% per quarter.
    """
    return ((1 + annual_pct / 100) ** (1 / 4) - 1) * 100.0


# def get_quarterly_expectations(base_fy_sales: float, annual_growth_pct: float) -> dict:
#     """Calculate expected TTM values as quarters progress through the year.
    
#     When Q1 FY27 arrives: TTM = base_fy Ã— (1 + quarterly_rate)^1
#     When Q2 FY27 arrives: TTM = base_fy Ã— (1 + quarterly_rate)^2
#     etc.
#     """
#     quarterly_rate = (1 + annual_growth_pct / 100) ** (1/4) - 1
    
#     expectations = {}
#     for q in range(1, 5):
#         ttm_multiplier = (1 + quarterly_rate) ** q
#         expected_ttm = base_fy_sales * ttm_multiplier
#         expected_growth = (ttm_multiplier - 1) * 100
#         expectations[f"Q{q}"] = {
#             "ttm": expected_ttm,
#             "growth_pct": expected_growth,
#             "multiplier": ttm_multiplier
#         }
    
#     return expectations


def get_quarterly_revenue(ticker: yf.Ticker) -> list[tuple[str, float]]:
    """
    Returns [(quarter_label, revenue_in_crores), ...] sorted oldest-first.
    Uses quarterly income statement from yfinance.
    """
    q_stmt = ticker.quarterly_financials
    if q_stmt is None or q_stmt.empty:
        return []

    preferred_row_keywords = [
        "operating revenue",
        "net sales",
        "revenue from operations",
        "total revenue",
        "revenue",
    ]
    rev_row = None
    for keyword in preferred_row_keywords:
        for idx in q_stmt.index:
            if keyword in str(idx).lower():
                rev_row = idx
                break
        if rev_row is not None:
            break

    if rev_row is None:
        return []

    CRORE = 10_000_000
    records: list[tuple[str, float]] = []
    for col, val in q_stmt.loc[rev_row].items():
        try:
            amount = float(val) / CRORE
        except (TypeError, ValueError):
            continue
        label = col.strftime("%b'%y") if hasattr(col, "strftime") else str(col)
        records.append((label, amount))

    # yfinance returns newest first; reverse to oldest-first
    records.reverse()
    return records


# ── ROCE cache (Outputs/roce_cache.json) ─────────────────────────────────
import threading as _threading
_ROCE_CACHE_FILE = os.path.join(os.path.dirname(__file__), "Outputs", "roce_cache.json")
_cache_lock = _threading.Lock()  # prevents concurrent threads from corrupting the cache file

def _load_roce_cache() -> dict:
    import json
    if os.path.exists(_ROCE_CACHE_FILE):
        try:
            with open(_ROCE_CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            return {}  # corrupted cache — treat as empty, will re-fetch
    return {}

def _save_roce_cache(cache: dict) -> None:
    import json, time
    os.makedirs(os.path.dirname(_ROCE_CACHE_FILE), exist_ok=True)
    # Write to .tmp then rename — atomic on all OSes, prevents partial-read corruption
    tmp_path = _ROCE_CACHE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    for attempt in range(5):  # retry on Windows file lock
        try:
            os.replace(tmp_path, _ROCE_CACHE_FILE)
            return
        except PermissionError:
            time.sleep(0.2)
    os.replace(tmp_path, _ROCE_CACHE_FILE)  # final attempt, let it raise if still locked

def get_roce_cached(symbol_plain: str, ticker: yf.Ticker) -> tuple[float | None, float | None, dict]:
    """Returns (roce_pct, ttm_rev_cr, annual_revenue_dict) from cache if fresh, else fetches and caches."""
    from datetime import date

    with _cache_lock:
        cache = _load_roce_cache()
        entry = cache.get(symbol_plain)
        if entry:
            age = (date.today() - date.fromisoformat(entry["fetched_on"])).days
            annual = entry.get("annual_revenue") or {}
            has_valid_annual = _has_complete_screener_annual(annual)
            if age <= ROCE_CACHE_DAYS and has_valid_annual and entry.get("ttm_rev_cr") is not None:
                return entry.get("roce_pct"), entry.get("ttm_rev_cr"), entry.get("annual_revenue") or {}

    # Fetch outside the lock so threads don't block each other on network I/O
    roce, ttm, annual = get_roce_and_ttm(ticker)

    with _cache_lock:
        cache = _load_roce_cache()  # re-read to pick up any writes from other threads
        cache[symbol_plain] = {
            "roce_pct": roce,
            "ttm_rev_cr": ttm,
            "annual_revenue": annual,
            "fetched_on": date.today().isoformat(),
        }
        _save_roce_cache(cache)  # inside lock — prevents concurrent writes to .tmp
    return roce, ttm, annual


def get_roce_and_ttm(ticker: yf.Ticker) -> tuple[float | None, float | None, dict]:
    """
    Fetches ROCE, TTM Sales, and annual revenue from screener.in only.
    Returns (roce_pct, ttm_rev_cr, annual_revenue_dict).
    """
    symbol_plain = ticker.ticker.split(".")[0]
    page_html = _fetch_screener_html(symbol_plain, retries=3, timeout=12)
    if not page_html:
        return None, None, {}

    return _parse_screener_financials(page_html)


def get_roce(ticker: yf.Ticker) -> float | None:
    """
    Fetches ROCE directly from screener.in to match the values shown there.
    Falls back to yfinance calculation if screener.in is unreachable.
    """
    # --- Primary: scrape screener.in ---
    try:
        import re
        import urllib.request

        # Strip .NS / .BO suffix to get plain NSE symbol
        symbol_plain = ticker.ticker.split(".")[0]
        url = f"https://www.screener.in/company/{symbol_plain}/consolidated/"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # screener.in renders ROCE as: <span class="name">ROCE</span> ... <span class="number">10.3</span>
        match = re.search(
            r'ROCE[\s\S]{0,300}?<span class="number">([\d.]+)</span>',
            html,
            re.IGNORECASE,
        )
        if match:
            return float(match.group(1))

    except Exception:
        pass  # fall through to yfinance

    # --- Fallback: yfinance calculation ---
    try:
        income = ticker.financials
        balance = ticker.balance_sheet

        if income is None or income.empty or balance is None or balance.empty:
            return None

        operating_profit_val = None
        for preferred in ("ebitda", "normalized ebitda", "ebit", "operating income"):
            for label in income.index:
                if str(label).strip().lower() == preferred:
                    operating_profit_val = float(income.loc[label].iloc[0])
                    break
            if operating_profit_val is not None:
                break

        if operating_profit_val is None:
            return None

        total_assets = None
        for label in balance.index:
            if "total assets" in str(label).strip().lower():
                total_assets = float(balance.loc[label].iloc[0])
                break

        current_liabilities = None
        for label in balance.index:
            normalized = str(label).strip().lower()
            if normalized in ("current liabilities", "total current liabilities"):
                current_liabilities = float(balance.loc[label].iloc[0])
                break
        if current_liabilities is None:
            for label in balance.index:
                normalized = str(label).strip().lower()
                if "current liabilities" in normalized and "non current" not in normalized:
                    current_liabilities = float(balance.loc[label].iloc[0])
                    break

        if total_assets is None or current_liabilities is None:
            return None

        capital_employed = total_assets - current_liabilities
        if capital_employed <= 0:
            return None

        return round((operating_profit_val / capital_employed) * 100, 1)

    except Exception:
        return None


def get_ttm_revenue(ticker: yf.Ticker) -> float | None:
    """
    Returns TTM (Trailing Twelve Months) revenue in crores from ticker.info.
    TTM is the sum of the last 12 months of revenue.
    """
    info = ticker.info or {}
    total_rev = info.get("totalRevenue")
    if total_rev is None or total_rev <= 0:
        return None
    
    CRORE = 10_000_000
    return float(total_rev) / CRORE


def get_current_price(ticker: yf.Ticker) -> float | None:
    """Returns current stock price from ticker.info."""
    info = ticker.info or {}
    current_price = info.get("currentPrice")
    if current_price is None:
        current_price = info.get("regularMarketPrice")
    return float(current_price) if current_price else None


def get_price_history(symbol: str, years: int = 2) -> tuple[float | None, float | None, float | None, str | None]:
    """
    Returns (current_price, price_2years_ago, price_change_pct, date_used).
    price_change_pct = ((current - old) / old) * 100
    Fetches price from exact date 2 years ago (or closest available).
    """
    from datetime import datetime, timedelta
    
    ticker = yf.Ticker(symbol)
    
    # Get current price
    current_price = get_current_price(ticker)
    if current_price is None:
        return None, None, None, None
    
    # Calculate exact date from years ago
    target_date = datetime.now() - timedelta(days=365 * years)
    
    try:
        # Fetch historical data for a wider range to ensure we get the target date
        hist = ticker.history(period=f"{years + 1}y")
        if hist is None or hist.empty:
            return current_price, None, None, None
        
        # Remove timezone from index for comparison
        hist_index = hist.index.tz_localize(None) if hasattr(hist.index, 'tz_localize') else hist.index
        
        # Find the row closest to target_date
        min_delta = float('inf')
        closest_price = None
        closest_date = None
        
        for idx, price in zip(hist_index, hist['Close']):
            delta = abs((idx - target_date).total_seconds())
            if delta < min_delta:
                min_delta = delta
                closest_price = float(price)
                closest_date = idx.strftime("%d-%b-%Y")
        
        if closest_price is None or closest_price <= 0:
            return current_price, None, None, None
        
        # Calculate percentage change
        price_change_pct = ((current_price - closest_price) / closest_price) * 100
        
        return current_price, closest_price, price_change_pct, closest_date
    except Exception as e:
        print(f"  [Warning] Could not fetch price history: {e}")
        return current_price, None, None, None


def check_sales_cagr(
    symbol: str,
    years: int = YEARS,
    min_cagr: float = MIN_SALES_CAGR_PCT,
) -> dict:
    """
    Runs the screener for one symbol and returns a result dict.
    Also prints the detailed analysis to stdout.
    """
    result: dict = {
        "symbol": symbol,
        "industry_group": None,
        "cagr_pct": None,
        "cagr_status": None,
        "base_fy": None,
        "end_fy": None,
        "base_rev_cr": None,
        "end_rev_cr": None,
        "ttm_rev_cr": None,
        "ttm_vs_end_fy_pct": None,
        "current_price": None,
        "price_2y_ago": None,
        "price_2y_change_pct": None,
        "roce_pct": None,
        "error": None,
    }

    print(f"\nStock  : {symbol}")
    print(f"Filter : Sales CAGR >= {min_cagr}% over last {years} years")
    print("-" * 45)

    # Fetch screener.in data first so annual_revenue cache is warm before get_annual_revenue reads it
    ticker = yf.Ticker(symbol)
    symbol_plain = symbol.split(".")[0]
    roce, ttm_revenue, _ = get_roce_cached(symbol_plain, ticker)

    revenue, currency = get_annual_revenue(symbol)

    if len(revenue) < years + 1:
        msg = (
            f"Not enough data. "
            f"Need {years + 1} years, got {len(revenue)}: {list(revenue.keys())}"
        )
        print(f"  [SKIP] {msg}")
        result["error"] = msg
        return result

    years_list = list(revenue.keys())

    # Take the most recent (years+1) data points for the CAGR window
    window = years_list[-(years + 1):]
    base_fy, end_fy = window[0], window[-1]
    base_rev, end_rev = revenue[base_fy], revenue[end_fy]

    result["base_fy"] = base_fy
    result["end_fy"] = end_fy
    result["base_rev_cr"] = round(base_rev, 2)
    result["end_rev_cr"] = round(end_rev, 2)

    print(f"  Currency : {currency}  |  Values in Crores (matches screener.in)")
    print("  Revenue data (all available):")
    for fy, rev in revenue.items():
        if fy == base_fy:
            marker = "  <-- base"
        elif fy == end_fy:
            marker = "  <-- end"
        else:
            marker = ""
        print(f"    {fy}: {rev:>10,.2f} Cr{marker}")

    if ttm_revenue is not None:
        ttm_growth = ((ttm_revenue / end_rev) - 1) * 100 if end_rev > 0 else 0
        result["ttm_rev_cr"] = round(ttm_revenue, 2)
        result["ttm_vs_end_fy_pct"] = round(ttm_growth, 1)
        print(f"    TTM:  {ttm_revenue:>10,.2f} Cr  (current, ↑{ttm_growth:+.1f}% vs {end_fy})")

    result["roce_pct"] = roce

    cagr = compute_cagr(base_rev, end_rev, years)

    if cagr is None:
        msg = f"Could not compute CAGR (base={base_rev}, end={end_rev})"
        print(f"\n  [ERROR] {msg}")
        result["error"] = msg
        return result

    cagr_status = "PASS" if cagr >= min_cagr else "FAIL"
    result["cagr_pct"] = round(cagr, 1)
    result["cagr_status"] = cagr_status
    print(f"\n  CAGR ({base_fy} -> {end_fy}, {years}Y) : {cagr:.1f}%  [{cagr_status}]")

    # â”€â”€ Price Movement â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print(f"\n  Price Movement (Last {years} Years):")
    current_price, old_price, price_change, price_date = get_price_history(symbol, years)

    if current_price is not None:
        result["current_price"] = round(current_price, 2)
        print(f"    Current Price: â‚¹{current_price:>10,.2f}")
        if old_price is not None and price_date is not None:
            result["price_2y_ago"] = round(old_price, 2)
            result["price_2y_change_pct"] = round(price_change, 1)
            print(f"    Price on {price_date}: â‚¹{old_price:>10,.2f}")
            print(f"    % Away:       {price_change:>+10.1f}%")
        else:
            print(f"    Price {years}Y ago: Not available")
    else:
        print(f"    Current Price: Not available")

    # â”€â”€ Layer 2: Quarterly TTM Expectations (During Current Year) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if ttm_revenue is not None:
        print(f"\n  Layer 2: Quarterly TTM Expectations (Current FY)")
        print("  " + "-" * 43)
        print(f"  (As new quarters arrive, TTM should grow progressively)")
        print(f"  Base Year ({end_fy}): {end_rev:>10,.2f} Cr")
        print(f"  Current TTM:          {ttm_revenue:>10,.2f} Cr  (â†‘{((ttm_revenue / end_rev) - 1) * 100:+.1f}%)")
        print(f"  Expected Annual Growth: {min_cagr}%")
        print()

    # â”€â”€ Final verdict â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print("\n" + "=" * 45)
    print(f"  SUMMARY")
    print("=" * 45)
    print(f"  Layer 1 (Historical): {cagr_status}")

    if ttm_revenue is not None:
        print(f"  Layer 2 (Quarterly): Available (TTM = {ttm_revenue:,.0f} Cr)")
    else:
        print(f"  Layer 2 (Quarterly): No TTM data")

    if roce is not None:
        print(f"  ROCE (Current):      {roce:.1f}%")
    else:
        print(f"  ROCE (Current):      Not available")

    print("=" * 45)
    return result


def load_symbols_from_csv(filepath: str) -> list[tuple[str, str, str]]:
    """
    Reads the input CSV and returns [(company_name, nse_symbol, industry_group), ...].
    Skips rows where NSE Code is blank.
    """
    symbols = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            nse_code = row.get("NSE Code", "").strip()
            name = row.get("Name", "").strip()
            industry = row.get("Industry Group", "").strip()
            if nse_code:
                symbols.append((name, f"{nse_code}.NS", industry))
    return symbols



# ── Google Sheets config ───────────────────────────────────────────────────
GSHEET_SPREADSHEET_NAME = "growth gap strategy"
GSHEET_TAB_NAME         = "growth gap strategy"
CREDENTIALS_FILE        = os.path.join(os.path.dirname(__file__), "credentials.json")
TOKEN_FILE              = os.path.join(os.path.dirname(__file__), "token.json")


def _get_gspread_client():
    """Authenticate via OAuth2 desktop flow and return authorised gspread client."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    import gspread

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token_fh:
            token_fh.write(creds.to_json())
    return gspread.authorize(creds)


def _clean(val):
    """Converts NaN/inf floats to empty string so JSON serialization doesn't crash."""
    import math
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return ""
    return val


def write_results_gsheet(results: list[dict], names: dict[str, str]) -> str:
    """
    Writes screener results to Google Sheets.
    - Creates the spreadsheet if it does not exist.
    - Creates or clears the tab GSHEET_TAB_NAME and overwrites data.
    - Column B (Symbol) is used by the GOOGLEFINANCE live-price formula.
    Returns the spreadsheet URL.
    """
    import gspread
    from gspread.exceptions import SpreadsheetNotFound

    client = _get_gspread_client()

    # Open or create spreadsheet
    try:
        sh = client.open(GSHEET_SPREADSHEET_NAME)
        print(f"  Opened existing sheet: {GSHEET_SPREADSHEET_NAME}")
    except SpreadsheetNotFound:
        sh = client.create(GSHEET_SPREADSHEET_NAME)
        print(f"  Created new sheet: {GSHEET_SPREADSHEET_NAME}")

    # Open or create tab
    try:
        ws = sh.worksheet(GSHEET_TAB_NAME)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=GSHEET_TAB_NAME, rows=500, cols=20)

    # Use the most recent end_fy across all results so headers reflect the latest FY, not first processed
    base_fy_label, end_fy_label = None, None
    for r in sorted(results, key=lambda x: x.get("end_fy") or "", reverse=True):
        if r.get("base_fy") and r.get("end_fy"):
            base_fy_label, end_fy_label = r["base_fy"], r["end_fy"]
            break
    base_rev_col = f"Base Rev {base_fy_label} (Cr)" if base_fy_label else "Base Rev (Cr)"
    end_rev_col  = f"End Rev {end_fy_label} (Cr)"  if end_fy_label  else "End Rev (Cr)"

    headers = [
        "Name", "Symbol", "Industry Group",
        base_rev_col, end_rev_col,
        "2Y CAGR (%)", "CAGR Status",
        "TTM Rev (Cr)", "TTM vs End FY (%)",
        "Current Price (INR)", "Price 2Y Ago (INR)", "% Away",
        "ROCE (%)",
        "Error",
    ]

    rows = [headers]
    for row_idx, r in enumerate(results, start=2):  # row 1 is header
        nse_sym = r["symbol"].replace(".NS", "").replace(".BO", "")
        live_price_formula = f'=GOOGLEFINANCE("NSE:{nse_sym}","price")'
        # 2Y price change recalculated live: ((live_price - price_2y_ago) / price_2y_ago) * 100
        if r["price_2y_ago"] is not None:
            price_change_formula = f"=(J{row_idx}-K{row_idx})/K{row_idx}*100"
        else:
            price_change_formula = ""
        rows.append([
            names.get(r["symbol"], r["symbol"]),
            r["symbol"],
            r.get("industry_group") or "",
            _clean(r["base_rev_cr"])       if r["base_rev_cr"]       is not None else "",
            _clean(r["end_rev_cr"])        if r["end_rev_cr"]        is not None else "",
            _clean(r["cagr_pct"])          if r["cagr_pct"]          is not None else "",
            r["cagr_status"]               or "",
            _clean(r["ttm_rev_cr"])        if r["ttm_rev_cr"]        is not None else "",
            _clean(r["ttm_vs_end_fy_pct"]) if r["ttm_vs_end_fy_pct"] is not None else "",
            live_price_formula,
            _clean(r["price_2y_ago"])      if r["price_2y_ago"]      is not None else "",
            price_change_formula,
            _clean(r["roce_pct"])          if r["roce_pct"]          is not None else "",
            r["error"]                     or "",
        ])

    ws.update(rows, value_input_option="USER_ENTERED")

    # Basic header formatting: bold + freeze row 1
    ws.format("A1:N1", {"textFormat": {"bold": True}})
    ws.freeze(rows=1)

    # Green highlight on % Away only when CAGR Status is PASS and % Away <= 0.
    sh.batch_update({"requests": [{
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": ws.id,
                    "startRowIndex": 1,       # skip header row
                    "startColumnIndex": 11,   # col L (0-based), % Away shifted by new Industry Group col
                    "endColumnIndex": 12,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": "=AND($G2=\"PASS\",$L2<=0)"}],
                    },
                    "format": {
                        "backgroundColor": {"red": 0.20, "green": 0.70, "blue": 0.32},
                        "textFormat": {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}},
                    },
                },
            },
            "index": 0,
        }
    }]})

    return sh.url
def write_results_csv(results: list[dict], names: dict[str, str], output_path: str) -> None:
    """Writes screener results to a CSV file."""
    # Use the most recent end_fy across all results for column headers
    base_fy_label = None
    end_fy_label = None
    for r in sorted(results, key=lambda x: x.get("end_fy") or "", reverse=True):
        if r.get("base_fy") and r.get("end_fy"):
            base_fy_label = r["base_fy"]
            end_fy_label = r["end_fy"]
            break

    base_rev_col = f"Base Rev {base_fy_label} (Cr)" if base_fy_label else "Base Rev (Cr)"
    end_rev_col = f"End Rev {end_fy_label} (Cr)" if end_fy_label else "End Rev (Cr)"

    fieldnames = [
        "Name", "Symbol", "Industry Group",
        base_rev_col, end_rev_col,
        "2Y CAGR (%)", "CAGR Status",
        "TTM Rev (Cr)", "TTM vs End FY (%)",
        "Current Price (INR)", "Price 2Y Ago (INR)", "% Away",
        "ROCE (%)",
        "Error",
    ]
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "Name":                  names.get(r["symbol"], r["symbol"]),
                "Symbol":                r["symbol"],
                "Industry Group":        r.get("industry_group") or "",
                base_rev_col:            r["base_rev_cr"] if r["base_rev_cr"] is not None else "",
                end_rev_col:             r["end_rev_cr"] if r["end_rev_cr"] is not None else "",
                "2Y CAGR (%)":           r["cagr_pct"] if r["cagr_pct"] is not None else "",
                "CAGR Status":           r["cagr_status"] or "",
                "TTM Rev (Cr)":          r["ttm_rev_cr"] if r["ttm_rev_cr"] is not None else "",
                "TTM vs End FY (%)":     r["ttm_vs_end_fy_pct"] if r["ttm_vs_end_fy_pct"] is not None else "",
                "Current Price (INR)":   r["current_price"] if r["current_price"] is not None else "",
                "Price 2Y Ago (INR)":    r["price_2y_ago"] if r["price_2y_ago"] is not None else "",
                "% Away":               r["price_2y_change_pct"] if r["price_2y_change_pct"] is not None else "",
                "ROCE (%)":              r["roce_pct"] if r["roce_pct"] is not None else "",
                "Error":                 r["error"] or "",
            })
if __name__ == "__main__":
    import time
    from datetime import datetime
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    args = parse_args(sys.argv[1:])
    OUTPUT_MODE = args.output
    INPUT_FILE = INPUT_FILES[args.input]

    symbols = load_symbols_from_csv(INPUT_FILE)
    if args.sample and args.sample < len(symbols):
        import random
        random.seed(42)  # fixed seed so same sample is reproducible for testing
        symbols = random.sample(symbols, args.sample)
        print(f"Sampled {args.sample} stocks randomly (seed=42 for reproducibility)")
    total = len(symbols)
    print(f"Loaded {total} stocks from: {INPUT_FILE}")
    print(f"Workers: {WORKERS} | Checkpoint every {CHECKPOINT_EVERY} | ROCE cache {ROCE_CACHE_DAYS} days\n")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(os.path.dirname(__file__), "Outputs")
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"screener_results_{timestamp}.csv")

    all_results: list[dict] = []
    name_map: dict[str, str] = {sym: name for name, sym, _ in symbols}
    industry_map: dict[str, str] = {sym: ind for name, sym, ind in symbols}
    errors: list[tuple[str, str]] = []
    lock = threading.Lock()
    completed = 0

    def _process(item: tuple[str, str, str]) -> dict:
        name, symbol, industry = item
        try:
            result = check_sales_cagr(symbol)
            result["industry_group"] = industry
            return result
        except Exception as e:
            print(f"  [ERROR] {symbol}: {e}\n")
            return {"symbol": symbol, "industry_group": industry, "error": str(e),
                    "cagr_pct": None, "cagr_status": None,
                    "base_fy": None, "end_fy": None,
                    "base_rev_cr": None, "end_rev_cr": None,
                    "ttm_rev_cr": None, "ttm_vs_end_fy_pct": None,
                    "current_price": None, "price_2y_ago": None,
                    "price_2y_change_pct": None, "roce_pct": None}

    # Split into batches of WORKERS; delay between batches, not between every stock
    batches = [symbols[i:i + WORKERS] for i in range(0, total, WORKERS)]
    for batch_num, batch in enumerate(batches, 1):
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(_process, item): item for item in batch}
            for future in as_completed(futures):
                result = future.result()
                name, symbol, industry = futures[future]
                with lock:
                    all_results.append(result)
                    completed += 1
                    if result.get("error"):
                        errors.append((name, symbol))
                    print(f"  [{completed}/{total}] done: {symbol}")
                    if completed % CHECKPOINT_EVERY == 0:
                        write_results_csv(all_results, name_map, output_file)
                        print(f"  [Checkpoint] Saved {completed}/{total} -> {output_file}")

        if batch_num < len(batches):
            time.sleep(DELAY_BETWEEN_STOCKS)

    output_file_final = None
    sheet_url = None

    if OUTPUT_MODE in ("csv", "both"):
        write_results_csv(all_results, name_map, output_file)
        output_file_final = output_file
        print(f"  CSV saved: {output_file}")

    if OUTPUT_MODE in ("gsheet", "both"):
        try:
            print("\nUploading to Google Sheets...")
            sheet_url = write_results_gsheet(all_results, name_map)
            print(f"  Google Sheet updated: {sheet_url}")
        except Exception as gsheet_err:
            print(f"  [Warning] Google Sheets upload failed: {gsheet_err}")

    print("\n" + "=" * 55)
    print(f"  BATCH COMPLETE: {total} stocks processed")
    if output_file_final:
        print(f"  CSV saved   : {output_file_final}")
    if sheet_url:
        print(f"  Google Sheet: {sheet_url}")
    if errors:
        print(f"  Errors/Skipped : {len(errors)}")
        for name, sym in errors:
            print(f"    - {name} ({sym})")
    print("=" * 55)











