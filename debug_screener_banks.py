#!/usr/bin/env python3
"""Standalone Screener-only bank parser diagnostic.

- Fetches only from screener.in (no yfinance fallback)
- Parses Profit & Loss revenue row, FY headers, ROCE, and TTM
- Prints diagnostics to console
"""

from __future__ import annotations

import argparse
import html
import json
import re
import urllib.error
import urllib.request
from typing import Any

DEFAULT_SYMBOLS = [
    "AUBANK.NS",
    "AXISBANK.NS",
    "MAHABANK.NS",
    "BANDHANBNK.NS",
    "CANBK.NS",
    "CUB.NS",
    "IDFCFIRSTB.NS",
    "IDBI.NS",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "XMLHttpRequest",
}


def clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw)
    text = html.unescape(text)
    return " ".join(text.split()).strip()


def extract_company_id(page_html: str) -> str | None:
    # Current screener layout has data-company-id before id="company-info".
    m = re.search(r'data-company-id="(\d+)"[^>]*id="company-info"', page_html)
    return m.group(1) if m else None


def parse_screener(symbol: str) -> dict[str, Any]:
    symbol_plain = symbol.split(".")[0]
    url = f"https://www.screener.in/company/{symbol_plain}/consolidated/"

    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        page_html = resp.read().decode("utf-8", errors="replace")

    result: dict[str, Any] = {
        "symbol": symbol,
        "symbol_plain": symbol_plain,
        "url": url,
        "company_id": extract_company_id(page_html),
        "roce": None,
        "profit_loss_found": False,
        "has_ttm_header": False,
        "all_period_headers": [],
        "fy_labels": [],
        "revenue_row_label": None,
        "revenue_numbers": [],
        "annual_map": {},
        "base_fy24": None,
        "end_fy26": None,
        "ttm": None,
        "schedule_api_status": None,
        "schedule_api_keys": [],
    }

    m = re.search(r'ROCE[\s\S]{0,300}?<span class="number">([\d.]+)</span>', page_html, re.IGNORECASE)
    if m:
        result["roce"] = float(m.group(1))

    pl = re.search(
        r'<section[^>]*id="profit-loss"[^>]*>(.*?)</section>',
        page_html,
        re.DOTALL | re.IGNORECASE,
    )
    if pl:
        result["profit_loss_found"] = True
        pl_html = pl.group(1)

        th_values_raw = re.findall(r"<th[^>]*>(.*?)</th>", pl_html, re.DOTALL | re.IGNORECASE)
        th_values = [clean_text(t) for t in th_values_raw]
        th_values = [t for t in th_values if t]
        result["all_period_headers"] = th_values

        mar_years = re.findall(r"\bMar\s+(\d{4})\b", " | ".join(th_values))
        fy_labels = [f"FY{year[2:]}" for year in mar_years]
        result["fy_labels"] = fy_labels

        result["has_ttm_header"] = any(h.upper() == "TTM" for h in th_values)

        revenue_row_html = None
        revenue_label = None
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", pl_html, re.DOTALL | re.IGNORECASE):
            first_td = re.search(r'<td[^>]*class="[^"]*text[^"]*"[^>]*>(.*?)</td>', tr, re.DOTALL | re.IGNORECASE)
            if not first_td:
                continue
            label = clean_text(first_td.group(1)).lower()
            if label.startswith("revenue") or label.startswith("sales"):
                revenue_row_html = tr
                revenue_label = clean_text(first_td.group(1))
                break

        result["revenue_row_label"] = revenue_label

        if revenue_row_html:
            nums = re.findall(r"<td[^>]*>\s*([\d,]+)\s*</td>", revenue_row_html)
            result["revenue_numbers"] = nums

            if nums:
                if result["has_ttm_header"]:
                    result["ttm"] = float(nums[-1].replace(",", ""))
                    annual_vals = nums[:-1]
                else:
                    annual_vals = nums

                relevant_labels = fy_labels[-len(annual_vals):] if fy_labels else []
                annual_map: dict[str, float] = {}
                for fy, val in zip(relevant_labels, annual_vals):
                    annual_map[fy] = float(val.replace(",", ""))

                result["annual_map"] = annual_map
                result["base_fy24"] = annual_map.get("FY24")
                result["end_fy26"] = annual_map.get("FY26")

    if result["company_id"]:
        api_url = (
            f"https://www.screener.in/api/company/{result['company_id']}/schedules/"
            f"?parent=Revenue&section=profit-loss&consolidated="
        )
        try:
            req = urllib.request.Request(api_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            result["schedule_api_status"] = "ok"
            if isinstance(payload, dict):
                result["schedule_api_keys"] = list(payload.keys())
        except urllib.error.HTTPError as exc:
            result["schedule_api_status"] = f"http_{exc.code}"
        except Exception as exc:
            result["schedule_api_status"] = f"error_{type(exc).__name__}"

    return result


def issue_summary(data: dict[str, Any]) -> str:
    issues: list[str] = []
    if not data["profit_loss_found"]:
        issues.append("profit-loss section missing")
    if len(data["fy_labels"]) == 0:
        issues.append("no FY labels")
    if len(data["revenue_numbers"]) == 0:
        issues.append("no revenue numeric values")
    if data["base_fy24"] is None:
        issues.append("FY24 missing")
    if data["end_fy26"] is None:
        issues.append("FY26 missing")
    if data["has_ttm_header"] and data["ttm"] is None:
        issues.append("TTM header present but TTM missing")
    return "; ".join(issues) if issues else "none"


def print_result(data: dict[str, Any]) -> None:
    print("=" * 88)
    print(f"Symbol                : {data['symbol']}")
    print(f"Base Rev FY24 (Cr)    : {data['base_fy24']}")
    print(f"End Rev FY26 (Cr)     : {data['end_fy26']}")
    print(f"TTM Rev (Cr)          : {data['ttm']}")
    print(f"ROCE                  : {data['roce']}")
    print(f"TTM header present    : {data['has_ttm_header']}")
    print(f"Revenue values count  : {len(data['revenue_numbers'])}")
    print(f"Schedule API status   : {data['schedule_api_status']}")
    print(f"Detected issues       : {issue_summary(data)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", nargs="*", help="NSE symbols like AUBANK.NS")
    args = parser.parse_args()

    symbols = args.symbols if args.symbols else DEFAULT_SYMBOLS

    for symbol in symbols:
        try:
            row = parse_screener(symbol)
            print_result(row)
        except Exception as exc:
            print("=" * 88)
            print(f"Symbol                : {symbol}")
            print(f"Fatal error           : {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
