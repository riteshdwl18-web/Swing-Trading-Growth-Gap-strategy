import re, urllib.request, sys

symbol = sys.argv[1] if len(sys.argv) > 1 else "DATAPATTNS"
url = f"https://www.screener.in/company/{symbol}/consolidated/"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=15) as resp:
    html = resp.read().decode("utf-8", errors="replace")

pl = re.search(r'id="profit-loss"(.*?)(?:id="[a-z])', html, re.DOTALL | re.IGNORECASE)
if not pl:
    print("P&L section NOT found")
    exit()

pl_html = pl.group(1)

# Show thead to understand column structure
thead = re.search(r'<thead>(.*?)</thead>', pl_html, re.DOTALL)
if thead:
    raw = re.sub(r'\s+', ' ', thead.group(1))
    print("THEAD:", raw[:600])

# Show all th text
ths = re.findall(r'<th[^>]*>(.*?)</th>', pl_html, re.DOTALL)
print("\nAll <th> values:")
for th in ths[:15]:
    print(" ", repr(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', th)).strip()))

# Show first data row structure
print("\nFirst data <tr> sample:")
for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', pl_html, re.DOTALL)[:8]:
    if '<td' in tr:
        print(repr(tr[:500]))
        print("---")
        break
