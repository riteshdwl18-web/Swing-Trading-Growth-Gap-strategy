import re, urllib.request, sys

symbol = sys.argv[1] if len(sys.argv) > 1 else "DATAPATTNS"
url = f"https://www.screener.in/company/{symbol}/consolidated/"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=15) as resp:
    html = resp.read().decode("utf-8", errors="replace")

pl = re.search(r'id="profit-loss"(.*?)(?:id="[a-z])', html, re.DOTALL | re.IGNORECASE)
if pl:
    pl_html = pl.group(1)
    years = re.findall(r'<th[^>]*>\s*(?:Mar|Sep|Jun|Dec)\s+(\d{4})\s*</th>', pl_html)
    fy_labels = [f"FY{y[2:]}" for y in years]
    has_ttm_col = bool(re.search(r'<th[^>]*>\s*TTM\s*</th>', pl_html, re.IGNORECASE))
    print(f"Symbol       : {symbol}")
    print(f"Year headers : {fy_labels}")
    print(f"Has TTM col  : {has_ttm_col}")

    revenue_row_html = None
    matched_label = None
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', pl_html, re.DOTALL):
        first_td = re.search(r'<td[^>]*class="[^"]*text[^"]*"[^>]*>(.*?)</td>', tr, re.DOTALL)
        if first_td:
            label = re.sub(r'<[^>]+>', '', first_td.group(1))
            label_clean = label.replace('\xa0', ' ').strip()
            if label_clean.lower().startswith(('sales', 'revenue')):
                matched_label = label_clean
                revenue_row_html = tr
                break

    print(f"Row label    : '{matched_label}'")

    if revenue_row_html:
        numbers = re.findall(r'<td[^>]*>\s*([\d,]+)\s*</td>', revenue_row_html)
        print(f"All values   : {numbers}")
        if not numbers:
            print("⚠ No values found — page uses JavaScript rendering, will fall back to yfinance")
        else:
            annual_vals = numbers[:-1] if has_ttm_col else numbers
            ttm_val = numbers[-1]
            annual = {}
            for fy, val in zip(fy_labels, annual_vals[-len(fy_labels):]):
                annual[fy] = float(val.replace(",", ""))
            print()
            print(f"Base Rev FY24 : {annual.get('FY24')} Cr")
            print(f"End Rev FY26  : {annual.get('FY26')} Cr")
            print(f"TTM           : {float(ttm_val.replace(',', ''))} Cr {'(TTM col)' if has_ttm_col else '(latest FY used as TTM)'}")
    else:
        print("Revenue row NOT found")
else:
    print("P&L section NOT found")
