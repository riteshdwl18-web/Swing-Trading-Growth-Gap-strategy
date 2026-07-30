import json
import re
import urllib.parse
import urllib.request

SYMBOLS = ["AUBANK", "BANDHANBNK", "CUB"]
HEADERS = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"}

for sym in SYMBOLS:
    page_url = f"https://www.screener.in/company/{sym}/consolidated/"
    req = urllib.request.Request(page_url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    m = re.search(r'data-company-id="(\d+)"[^>]*id="company-info"', html)
    company_id = m.group(1) if m else None
    print(f"SYMBOL={sym} company_id={company_id}")
    if not company_id:
        print("  Could not find company_id")
        continue

    api_url = (
        f"https://www.screener.in/api/company/{company_id}/schedules/"
        f"?parent=Revenue&section=profit-loss&consolidated="
    )
    req = urllib.request.Request(api_url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        text = resp.read().decode("utf-8", errors="replace")

    print(f"  API length: {len(text)}")
    data = json.loads(text)
    print(f"  Row keys: {list(data.keys())[:5]}")
    if data:
        first_key = next(iter(data))
        cols = list(data[first_key].keys())[:12]
        print(f"  First row ({first_key}) cols: {cols}")

    for metric in ["Sales", "Revenue", "Net Profit", "Operating Profit", "Price"]:
        chart_url = (
            f"https://www.screener.in/api/company/{company_id}/chart/"
            f"?q={urllib.parse.quote(metric)}&days=4000&consolidated=true"
        )
        try:
            req = urllib.request.Request(chart_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            datasets = payload.get("datasets") or []
            if not datasets:
                continue
            ds = datasets[0]
            points = ds.get("values") or ds.get("points") or ds.get("data") or []
            print(f"  Chart metric={metric!r} datasets={len(datasets)} points={len(points)}")
            if points:
                print(f"    sample first={points[0]} last={points[-1]}")
        except Exception as exc:
            print(f"  Chart metric={metric!r} error={exc}")
    print("-")
