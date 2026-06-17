# Single-Site PV Dashboard — Learning & Test Repo

A complete, self-contained solar monitoring dashboard for **one site** (Addo Spar).
Includes both the **API fetcher** and the **browser scraper** so you can test both approaches.

## What's in this repo

```
├── fetch.py                          # API approach (HTTP requests, fast, no browser)
├── scrape.py                         # Scraper approach (Playwright browser automation)
├── process.py                        # Processes raw data — reads JSON or XLSX
├── refresh_irradiation.py            # Fetches solar irradiation from Open-Meteo
├── sites/
│   └── addo-spar/
│       ├── index.html                # Individual site dashboard
│       └── data/
│           ├── processed.json        # Main data file (created by process.py)
│           └── history.json          # 30-day rolling history
├── data/
│   └── raw/
│       ├── addo-spar.json            # Raw API output (from fetch.py)
│       └── addo-spar.xlsx            # Raw XLSX report (from scrape.py)
└── .github/workflows/
    ├── fetch-and-process.yml         # Runs API fetch + process every 30 min
    ├── scrape-and-process.yml        # Runs browser scraper + process (manual trigger)
    └── refresh-irradiation.yml       # Fixes missing irradiation data
```

## Two ways to get data

| Approach | File | Speed | Dependencies | Secrets |
|----------|------|-------|-------------|---------|
| **API** | fetch.py | ~3 min | requests only | FUSIONSOLAR_API_USER + _PASS |
| **Scraper** | scrape.py | ~5 min | playwright + chromium | FUSIONSOLAR_USERNAME + _PASSWORD |

The API uses the Northbound API (separate credentials from portal login).
The scraper opens a real browser and downloads the XLSX report.
Both produce data that process.py can read.

## How it works

```
fetch.py (API)   ──→  data/raw/addo-spar.json  ──┐
                                                   ├──→  process.py  ──→  processed.json
scrape.py (browser) → data/raw/addo-spar.xlsx ──┘          │
                                                            ├── Fetches irradiation
                                                            ├── Calculates 30-day stats
                                                            ├── Determines OK/LOW/OFFLINE
                                                            ├── Sends Telegram alert
                                                            └── Updates history.json
```

process.py checks for JSON first (from the API), falls back to XLSX (from the scraper).

## Setup

### 1. Create the repo
- Create a new GitHub repository
- Drag and drop all these files into it
- Enable GitHub Pages: Settings → Pages → Branch: main

### 2. Add GitHub Secrets
Settings → Secrets and variables → Actions → New repository secret

**For the API approach (fetch.py):**

| Secret | Value |
|--------|-------|
| FUSIONSOLAR_API_USER | Northbound API username |
| FUSIONSOLAR_API_PASS | Northbound API password |

**For the scraper approach (scrape.py):**

| Secret | Value |
|--------|-------|
| FUSIONSOLAR_USERNAME | Portal login email |
| FUSIONSOLAR_PASSWORD | Portal login password |

**Optional (alerts):**

| Secret | Value |
|--------|-------|
| TELEGRAM_BOT_TOKEN | Bot token from @BotFather |
| TELEGRAM_CHAT_ID | Telegram group ID |

### 3. Run it

**API approach (automatic, every 30 min):**
The fetch-and-process workflow runs automatically.

**Scraper approach (manual trigger):**
Go to Actions → Scrape and Process → Run workflow.

**Locally:**
```bash
pip install requests pandas openpyxl

# API approach
FUSIONSOLAR_API_USER=user FUSIONSOLAR_API_PASS=pass python fetch.py
python process.py

# Scraper approach
pip install playwright && playwright install chromium
FUSIONSOLAR_USERNAME=email FUSIONSOLAR_PASSWORD=pass python scrape.py
python process.py
```

Then open sites/addo-spar/index.html in your browser.

### 4. View the dashboard
After the first successful run:
https://YOUR-USERNAME.github.io/REPO-NAME/sites/addo-spar/

## Customising for a different site

Edit the top of fetch.py, scrape.py, and process.py:

```python
SITE_NAME    = "Your Plant Name"      # Exact name in FusionSolar
SITE_SLUG    = "your-plant"           # Folder name (lowercase, hyphens)
STATION_CODE = "NE=12345678"          # From DISCOVER=1 (API only)
LAT, LON     = -33.00, 25.00          # GPS coordinates
```

Rename the sites/ folder to match your slug.

## Discovering your station code (API only)
```bash
FUSIONSOLAR_API_USER=user FUSIONSOLAR_API_PASS=pass DISCOVER=1 python fetch.py
```
