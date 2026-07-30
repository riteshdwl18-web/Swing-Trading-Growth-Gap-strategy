import re, urllib.request

url = "https://www.screener.in/company/AAVAS/consolidated/"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=15) as resp:
    html = resp.read().decode("utf-8", errors="replace")

# Look for JSON data embedded in script tags
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
for i, s in enumerate(scripts):
    if 'revenue' in s.lower() or 'FY25' in s or 'FY26' in s or '2683' in s or '2765' in s:
        print(f"Script {i} contains revenue/FY data:")
        print(s[:600])
        print("---")

# Also check for data-* attributes containing recent years
matches = re.findall(r'data-[^=]+=.[^"\']*(?:2025|2026|FY25|FY26)[^"\']*["\']', html)
print("\ndata-* attrs with 2025/2026:", matches[:5])


