#!/usr/bin/env python3
"""
Validation script — compares screener results CSV against live screener.in values.
Usage:
    python test_validate.py Outputs/screener_results_YYYYMMDD_HHMMSS.csv
    python test_validate.py Outputs/screener_results_YYYYMMDD_HHMMSS.csv --stocks AAVAS DIXON RELAXO
"""
import csv
import re
import sys
import time
import argparse
import urllib.request

PASS  = "PASS"
FAIL  = "FAIL"
TOLERANCE     = 5.0   # % tolerance for revenue comparison
DELAY_SECONDS = 3.0   # delay between screener.in requests to avoid rate limiting


def fetch_screener(symbol_plain: str) -> dict:
    """Fetch ROCE, TTM, and annual revenue directly from screener.in."""
    url = f"https://www.screener.in/company/{symbol_plain}/consolidated/"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    data: dict = {"symbol": symbol_plain}

    # ROCE
    m = re.search(r'ROCE[\s\S]{0,300}?<span class="number">([\d.]+)</span>', html, re.IGNORECASE)
    data["roce"] = float(m.group(1)) if m else None

    # P&L table
    pl = re.search(r'id="profit-loss"(.*?)(?:id="[a-z])', html, re.DOTALL | re.IGNORECASE)
    if pl:
        pl_html = pl.group(1)
        years = re.findall(r'<th[^>]*>\s*(?:Mar|Sep|Jun|Dec)\s+(\d{4})\s*</th>', pl_html)
        fy_labels = [f"FY{y[2:]}" for y in years]

        sales_row = re.search(
            r'<td[^>]*>\s*Sales\s*(?:<[^>]+>)?\s*\+?\s*(?:</[^>]+>)?\s*</td>(.*?)</tr>',
            pl_html, re.DOTALL | re.IGNORECASE,
        )
        if sales_row:
            numbers = re.findall(r'<td[^>]*>\s*([\d,]+)\s*</td>', sales_row.group(1))
            if numbers:
                data["ttm"] = float(numbers[-1].replace(",", ""))
                annual_vals = numbers[:-1]
                data["annual"] = {}
                for fy, val in zip(fy_labels, annual_vals[-len(fy_labels):]):
                    data["annual"][fy] = float(val.replace(",", ""))

    return data


def pct_diff(a, b):
    if a and b:
        return abs(a - b) / b * 100
    return None


def validate(csv_path: str, filter_symbols: list[str] | None = None):
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if filter_symbols:
        rows = [r for r in rows if r["Symbol"].split(".")[0] in filter_symbols]

    print(f"\nValidating {len(rows)} stocks from: {csv_path}\n")
    print(f"{'Symbol':<15} {'Field':<20} {'CSV':>12} {'screener.in':>12} {'Diff%':>8} {'Status'}")
    print("-" * 80)

    passed = failed = skipped = 0

    for row in rows:
        symbol = row["Symbol"].split(".")[0]
        try:
            live = fetch_screener(symbol)
            time.sleep(DELAY_SECONDS)
        except Exception as e:
            print(f"{symbol:<15} SKIP — could not fetch: {e}")
            skipped += 1
            continue

        checks = []

        # Find base/end rev columns dynamically
        base_col = next((k for k in row if "Base Rev" in k), None)
        end_col  = next((k for k in row if "End Rev" in k), None)

        if base_col and live.get("annual"):
            base_fy = re.search(r'FY\d+', base_col)
            if base_fy:
                live_base = live["annual"].get(base_fy.group())
                csv_base  = float(row[base_col]) if row[base_col] else None
                checks.append((f"Base Rev ({base_fy.group()})", csv_base, live_base))

        if end_col and live.get("annual"):
            end_fy = re.search(r'FY\d+', end_col)
            if end_fy:
                live_end = live["annual"].get(end_fy.group())
                csv_end  = float(row[end_col]) if row[end_col] else None
                checks.append((f"End Rev ({end_fy.group()})", csv_end, live_end))

        if row.get("TTM Rev (Cr)") and live.get("ttm"):
            checks.append(("TTM Rev", float(row["TTM Rev (Cr)"]) if row["TTM Rev (Cr)"] else None, live["ttm"]))

        if row.get("ROCE (%)") and live.get("roce"):
            checks.append(("ROCE", float(row["ROCE (%)"]) if row["ROCE (%)"] else None, live["roce"]))

        for field, csv_val, live_val in checks:
            diff = pct_diff(csv_val, live_val)
            if diff is None:
                status = "SKIP"
                skipped += 1
            elif diff <= TOLERANCE:
                status = "OK  ✓"
                passed += 1
            else:
                status = "FAIL ✗"
                failed += 1
            diff_str = f"{diff:.1f}%" if diff is not None else "N/A"
            csv_str  = f"{csv_val:,.2f}" if csv_val is not None else "N/A"
            live_str = f"{live_val:,.2f}" if live_val is not None else "N/A"
            print(f"{symbol:<15} {field:<20} {csv_str:>12} {live_str:>12} {diff_str:>8}   {status}")

    print("-" * 80)
    print(f"\nSummary: {passed} OK  |  {failed} FAILED  |  {skipped} SKIPPED")
    print(f"Tolerance used: ±{TOLERANCE}%\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_file", help="Path to screener results CSV")
    parser.add_argument("--stocks", nargs="+", metavar="SYMBOL",
                        help="Validate only these symbols (e.g. --stocks AAVAS DIXON)")
    args = parser.parse_args()
    validate(args.csv_file, args.stocks)
