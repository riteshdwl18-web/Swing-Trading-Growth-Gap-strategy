import re
import urllib.request

symbol = "ADANIENSOL"
url = f"https://www.screener.in/company/{symbol}/consolidated/"
print(f"Fetching: {url}")

req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=10) as resp:
    html = resp.read().decode("utf-8", errors="replace")

print(f"Page size: {len(html)} chars")

# Fixed pattern matching screener.in's actual HTML structure
match = re.search(
    r'ROCE[\s\S]{0,300}?<span class="number">([\d.]+)</span>',
    html, re.IGNORECASE
)
if match:
    print(f"FIXED pattern match -> ROCE: {match.group(1)}%")
else:
    print("FIXED pattern: NO MATCH")

# Show HTML around ROCE keyword for diagnosis
idx = html.lower().find("roce")
if idx != -1:
    print("\nHTML around 'roce' keyword:")
    print(repr(html[max(0, idx - 50): idx + 200]))
else:
    print("\n'roce' not found anywhere in page HTML")
