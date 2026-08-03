# SwingTrading – NSE Stock Screener & Portfolio Tracker

A Python toolkit for screening NSE-listed stocks based on the **Growth-Gap strategy** — identifying companies with consistent 2-year sales CAGR ≥ 15% and tracking their quarterly progress.

---

## Project Structure

```
SwingTrading/
├── Inputs/
│   └── growth-gap-strategy-mp.csv   # Watchlist of NSE stocks
├── stock_screener.py                # Core screener – CAGR analysis & price movement
├── portfolio_tracker.py             # Batch Excel tracker (multi-stock, quarterly updates)
├── excel_tracker.py                 # Single-stock Excel tracker with quarterly targets
└── requirements.txt                 # Python dependencies
```

---

## Strategy Overview

The screener applies a 3-layer check on each stock:

| Layer | What it checks | When |
|-------|---------------|------|
| **Layer 1** | Historical 2-year Sales CAGR ≥ 15% | After each FY ends |
| **Layer 2** | TTM revenue growing progressively vs last FY | Quarterly (live) |
| **Layer 3** | Rolling 2-year CAGR projection for next FY ≥ 15% | After next FY ends |

**PASS** = all layers on track. **FAIL** = review / exit position.

For the stock screener output, `2Y CAGR (%)` remains the historical growth check, while `Final Status` is based on both `2Y CAGR (%) >= 15%` and `TTM vs End FY (%) >= 5%`.

---

## Setup

### Prerequisites
- Python 3.10+

### Install dependencies

```bash
pip install -r requirements.txt
```

**Dependencies:** `pandas`, `yfinance`, `openpyxl`, `numpy`

---

## Google Sheets Integration (One-Time Setup)

Use this setup if you want `--output gsheet` or `--output both`.

### 1. Create Google Cloud project and enable APIs

1. Open Google Cloud Console and create/select a project.
2. Enable these APIs for the project:
  - Google Sheets API
  - Google Drive API

### 2. Create OAuth Desktop credentials

1. Go to **APIs & Services -> OAuth consent screen** and configure an app (External is fine for personal use).
2. Go to **APIs & Services -> Credentials**.
3. Click **Create Credentials -> OAuth client ID**.
4. Choose **Desktop app**.
5. Download the JSON file and save it in project root as:
  - `credentials.json`

### 3. Run first Google Sheets sync

Run:

```bash
python stock_screener.py --output gsheet --refresh
```

On first run:
1. A browser window opens for Google sign-in.
2. Approve permissions for Sheets and Drive.
3. The script stores your token locally as:
  - `token.json`

Next runs reuse `token.json` and do not prompt again unless token expires/revoked.

### 4. Optional: change destination sheet/tab names

In `stock_screener.py`, update:
- `GSHEET_SPREADSHEET_NAME`
- `GSHEET_TAB_NAME`

### 5. Verify output

After run completion, the console prints:
- `Google Sheet updated: <url>`

Open that URL to confirm headers, formulas, and status formatting.

---

## Usage

### 1. Stock Screener (`stock_screener.py`)

Screens all stocks in the input CSV and prints a detailed analysis for each.

#### Commands

**Run against the default watchlist (growth-gap):**
```bash
python stock_screener.py
```

**Run against Nifty 500:**
```bash
python stock_screener.py --input nifty-500
```

**Force a fresh Screener fetch and bypass cached values:**
```bash
python stock_screener.py --refresh
```

**Run against Nifty 500 v2 with Google Sheet output and refresh enabled:**
```bash
python stock_screener.py --input nifty-500 --output gsheet --refresh
```

**Save output to CSV (default):**
```bash
python stock_screener.py --output csv
```

**Save output to Google Sheet:**
```bash
python stock_screener.py --output gsheet
```

**Save to both CSV and Google Sheet:**
```bash
python stock_screener.py --output both
```

**Nifty 500 + Google Sheet:**
```bash
python stock_screener.py --input nifty-500 --output gsheet
```

**Nifty 500 + both outputs:**
```bash
python stock_screener.py --input nifty-500 --output both
```

#### Key config (top of file)

| Variable | Default | Description |
|----------|---------|-------------|
| `MIN_SALES_CAGR_PCT` | `15.0` | Minimum 2Y revenue CAGR to PASS |
| `YEARS` | `2` | CAGR window in years |
| `DELAY_BETWEEN_STOCKS` | `2` | Seconds between stocks (avoids rate limiting) |
| `CHECKPOINT_EVERY` | `25` | Save CSV progress every N stocks |
| `ROCE_CACHE_DAYS` | `90` | Days before ROCE is re-fetched from screener.in |

Use `--refresh` to bypass cached Screener data for the current run.

#### ROCE cache
ROCE and annual revenue are scraped from the consolidated Screener page and cached in `Outputs/roce_cache.json`.  
Subsequent runs within 90 days reuse the cache for speed.  
Use `--refresh` to ignore the cache for a run and fetch fresh Screener values.

#### Checkpoint / crash recovery
A CSV is written every 25 stocks to `Outputs/screener_results_{timestamp}.csv`.  
If the script crashes mid-run, partial results are already saved.

Input file is auto-loaded from `Inputs/growth-gap-strategy-mp.csv`.

**Sample output per stock:**
```
Stock  : RRKABEL.NS
Filter : Sales CAGR >= 15.0% over last 2 years
---------------------------------------------
  Revenue data (all available):
    FY24:   6,517.62 Cr  <-- base
    FY26:   9,587.00 Cr  <-- end
    TTM:   10,831.96 Cr  (current, +13.0% vs FY26)

  CAGR (FY24 -> FY26, 2Y) : 21.3%  [PASS]

  Price Movement (Last 2 Years):
    Current Price: ₹  2,569.80
    Price on 30-Jul-2024: ₹  1,783.07
    Distance:          +44.1%
```

---

### 2. Portfolio Tracker (`portfolio_tracker.py`)

Generates a formatted **Excel file** (`Portfolio_Tracker.xlsx`) with all stocks on a single sheet. Updates in place on re-runs.

**Run with the default CSV:**
```bash
python portfolio_tracker.py
```

**Run with a specific CSV:**
```bash
python portfolio_tracker.py Inputs/growth-gap-strategy-mp.csv
```

**Run for a single stock:**
```bash
python portfolio_tracker.py RELIANCE.NS
```

The Excel file contains three sheets:

| Sheet | Contents |
|-------|----------|
| **Master Tracker** | All stocks — CAGR, TTM, CMP, price change, PASS/FAIL (color-coded) |
| **Quarterly Updates** | Template to log actual TTM each quarter as results are announced |
| **Instructions** | How to use the tracker and interpret the data |

---

### 3. Excel Tracker (`excel_tracker.py`)

Generates a per-stock Excel file with quarterly TTM targets and a tracker template. Useful for deep-diving into a single stock.

```bash
python excel_tracker.py
```

Edit `SYMBOL = "RRKABEL.NS"` at the top of the file to change the stock. The output file is saved as `<SYMBOL>_tracking_<date>.xlsx`.

---

## Input File Format

`Inputs/growth-gap-strategy-mp.csv` — comma-separated with the following columns:

```csv
Name,BSE Code,NSE Code
360 ONE,542772,360ONE
RRKABEL,,RRKABEL
...
```

- **NSE Code** is used to fetch data via yfinance (`.NS` suffix is appended automatically).
- Rows with a blank `NSE Code` are skipped.

---

## Configuration

Key constants at the top of each script:

| Constant | Default | Description |
|----------|---------|-------------|
| `MIN_SALES_CAGR_PCT` | `15.0` | Minimum CAGR threshold (%) to PASS |
| `YEARS` | `2` | CAGR window in years |
| `INPUT_FILE` | `Inputs/growth-gap-strategy-mp.csv` | Watchlist path (screener) |
| `TRACKER_FILE` | `Portfolio_Tracker.xlsx` | Output Excel file (portfolio tracker) |

---

## Notes

- All revenue figures are in **Indian Crores (₹ Cr)**, matching the consolidated [screener.in](https://www.screener.in) display.
- **TTM** (Trailing Twelve Months) is sourced from Screener's consolidated Profit & Loss table.
- **Entry Price** is the stock's closing price exactly 2 years ago (closest available trading day).
- Data is fetched live from Yahoo Finance on every run; an internet connection is required.
- On Windows, the scripts output UTF-8 characters (₹, ↑, ≥) correctly without any extra configuration.
