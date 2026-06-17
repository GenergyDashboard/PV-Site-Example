"""
process.py — Single-site processor

Reads raw data from EITHER:
  - data/raw/addo-spar.json  (from fetch.py — the API approach)
  - data/raw/addo-spar.xlsx  (from scrape.py — the browser approach)

Whichever exists gets processed. JSON is preferred if both exist.

Then fetches irradiation, calculates 30-day statistics, determines status,
optionally sends Telegram alerts, and writes processed.json for the dashboard.
"""

import json, os, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    import pandas as pd
except ImportError:
    pd = None

# ── SITE CONFIG (must match fetch.py / scrape.py) ───────────
SITE_SLUG = "addo-spar"
SITE_NAME = "Addo Spar Smart Logger"
LAT       = -33.4710
LON       = 25.7530

# ── PATHS ────────────────────────────────────────────────────
HERE         = Path(__file__).parent
RAW_JSON     = HERE / "data" / "raw" / f"{SITE_SLUG}.json"
RAW_XLSX     = HERE / "data" / "raw" / f"{SITE_SLUG}.xlsx"
SITE_DIR     = HERE / "sites" / SITE_SLUG / "data"
HISTORY_FILE = SITE_DIR / "history.json"
OUTPUT_FILE  = SITE_DIR / "processed.json"
ALERT_FILE   = SITE_DIR / "alert_state.json"

SAST = timezone(timedelta(hours=2))
HISTORY_DAYS = 30


# ── READ RAW DATA ────────────────────────────────────────────

def read_api_json():
    """Read raw JSON from fetch.py (the API approach)."""
    raw = json.loads(RAW_JSON.read_text())
    return {
        "plant_name": raw.get("plant_name", SITE_NAME),
        "date":       raw.get("date", ""),
        "total_kwh":  round(float(raw.get("total_kwh", 0)), 2),
        "hourly":     raw.get("hourly_pv", [0.0] * 24),
        "last_hour":  raw.get("last_hour", 0),
        "source":     "API",
    }


def read_scraper_xlsx():
    """Read XLSX from scrape.py (the browser approach)."""
    if pd is None:
        print("  ❌ pandas not installed — can't read XLSX")
        return None

    df = pd.read_excel(RAW_XLSX, sheet_name=0, header=None)

    # Find plant name (row 1, col 1 typically)
    plant_name = ""
    for r in range(min(5, len(df))):
        for c in range(min(5, len(df.columns))):
            val = str(df.iloc[r, c]) if not pd.isna(df.iloc[r, c]) else ""
            if "spar" in val.lower() or "bmi" in val.lower() or "coega" in val.lower():
                plant_name = val
                break

    # Find header row (contains "Time" or "PV Yield")
    header_row = None
    pv_col = None
    time_col = None
    for r in range(min(15, len(df))):
        for c in range(len(df.columns)):
            val = str(df.iloc[r, c]).lower() if not pd.isna(df.iloc[r, c]) else ""
            if "pv yield" in val or "pv_yield" in val:
                pv_col = c
                header_row = r
            if "time" in val:
                time_col = c

    if header_row is None or pv_col is None:
        # Fallback: assume header at row 5, PV at col 4, time at col 0
        header_row = 5
        pv_col = 4
        time_col = 0

    # Parse data rows
    hourly = [0.0] * 24
    total = 0.0
    report_date = ""
    UTC_OFFSET = 2

    for idx in range(header_row + 1, len(df)):
        row = df.iloc[idx]
        time_val = row.iloc[time_col] if time_col is not None else None
        pv_val_raw = row.iloc[pv_col]

        if pd.isna(pv_val_raw) or pd.isna(time_val):
            continue

        try:
            pv_val = float(pv_val_raw)
        except (ValueError, TypeError):
            continue

        # Parse time
        if hasattr(time_val, 'hour'):
            hour = (time_val.hour + UTC_OFFSET) % 24
            if not report_date and hasattr(time_val, 'strftime'):
                report_date = time_val.strftime("%Y-%m-%d")
        else:
            ts = str(time_val)
            if " " in ts:
                report_date = ts.split(" ")[0]
                time_part = ts.split(" ")[1]
                hour = (int(time_part.split(":")[0]) + UTC_OFFSET) % 24
            else:
                continue

        hourly[hour] += round(pv_val, 4)
        total += pv_val

    hourly = [round(v, 2) for v in hourly]
    last_hour = max((h for h in range(24) if hourly[h] > 0), default=0)

    return {
        "plant_name": plant_name or SITE_NAME,
        "date":       report_date or datetime.now(SAST).strftime("%Y-%m-%d"),
        "total_kwh":  round(total, 2),
        "hourly":     hourly,
        "last_hour":  last_hour,
        "source":     "XLSX Scraper",
    }


# ── IRRADIATION ──────────────────────────────────────────────

def fetch_irradiation(date_str):
    for attempt in range(2):
        try:
            resp = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": LAT, "longitude": LON,
                        "hourly": "shortwave_radiation",
                        "start_date": date_str, "end_date": date_str},
                timeout=10)
            resp.raise_for_status()
            irrad = resp.json().get("hourly", {}).get("shortwave_radiation", [])
            while len(irrad) < 24: irrad.append(0)
            result = [0.0] * 24
            for h in range(24):
                if h + 1 <= 23:
                    result[h + 1] = round(irrad[h] or 0, 1)
            if sum(result) < 1:
                raise ValueError("Near zero")
            return result
        except Exception as e:
            if attempt < 1: time.sleep(3)
    return [0] * 24


# ── TELEGRAM ─────────────────────────────────────────────────

def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id: return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
    except Exception: pass


# ── HISTORY & STATS ──────────────────────────────────────────

def load_history():
    if HISTORY_FILE.exists():
        try: return json.loads(HISTORY_FILE.read_text())
        except Exception: pass
    return {}


def save_history(history, date_str, hourly_pv, irradiation, total_kwh, last_hour):
    history[date_str] = {
        "total_kwh": round(total_kwh, 2),
        "hourly": [round(v, 2) for v in hourly_pv],
        "irradiation": [round(v, 1) for v in irradiation],
        "last_hour": last_hour,
    }
    dates = sorted(history.keys())
    while len(dates) > HISTORY_DAYS:
        del history[dates.pop(0)]
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


def compute_stats(history):
    dates = sorted(history.keys())
    n = len(dates)
    if n == 0:
        return {"hourly_avg":[0]*24,"hourly_min":[0]*24,"hourly_max":[0]*24,
                "hourly_p25":[0]*24,"hourly_p75":[0]*24,"hourly_irrad_avg":[0]*24,
                "daily_avg":0,"daily_min":0,"daily_max":0,"sample_days":0}

    hourly_vals = [[] for _ in range(24)]
    irrad_sums = [0.0] * 24
    daily_totals = []

    for d in dates:
        day = history[d]
        hr = day.get("hourly", [])
        ir = day.get("irradiation", [])
        daily_totals.append(day.get("total_kwh", 0))
        for h in range(24):
            hourly_vals[h].append(hr[h] if h < len(hr) else 0)
            irrad_sums[h] += ir[h] if h < len(ir) else 0

    stats = {"sample_days": n}
    for key, func in [("hourly_avg", lambda v: sum(v)/len(v)), ("hourly_min", min), ("hourly_max", max)]:
        stats[key] = [round(func(hourly_vals[h]), 2) if hourly_vals[h] else 0 for h in range(24)]

    for h in range(24):
        s = sorted(hourly_vals[h])
        stats.setdefault("hourly_p25", [0]*24)[h] = s[len(s)//4] if len(s) >= 4 else stats["hourly_min"][h]
        stats.setdefault("hourly_p75", [0]*24)[h] = s[3*len(s)//4] if len(s) >= 4 else stats["hourly_max"][h]

    stats["hourly_irrad_avg"] = [round(irrad_sums[h]/n, 1) for h in range(24)]
    stats["daily_avg"] = round(sum(daily_totals)/n, 1)
    stats["daily_min"] = round(min(daily_totals), 1)
    stats["daily_max"] = round(max(daily_totals), 1)
    return stats


def determine_status(total_kwh, hourly_pv, stats, last_hour):
    if total_kwh <= 0: return "offline"
    avg = stats.get("daily_avg", 0)
    if avg <= 0: return "ok"
    ha = stats.get("hourly_avg", [0]*24)
    expected = sum(ha[5:last_hour+1])
    actual = sum(hourly_pv[5:last_hour+1])
    if expected > 0 and (actual / expected) < 0.5: return "low"
    return "ok"


# ── MAIN ─────────────────────────────────────────────────────

def main():
    print(f"🔄 Processing {SITE_NAME}")

    # Read from JSON (API) or XLSX (scraper) — JSON preferred
    data = None
    if RAW_JSON.exists():
        data = read_api_json()
        print(f"  📡 Source: {data['source']}")
    elif RAW_XLSX.exists():
        data = read_scraper_xlsx()
        if data:
            print(f"  📄 Source: {data['source']}")
    
    if not data:
        print(f"  ❌ No raw data found (checked .json and .xlsx)")
        sys.exit(1)

    date = data["date"]
    total_kwh = data["total_kwh"]
    hourly_pv = data["hourly"]
    last_hour = data["last_hour"]

    print(f"  📅 Date: {date} | Plant: {data['plant_name']}")
    print(f"  ⚡ {total_kwh} kWh | Last hour: {last_hour:02d}:00")

    # Irradiation
    history = load_history()
    existing_ir = history.get(date, {}).get("irradiation", [])
    if existing_ir and sum(existing_ir) > 10:
        irradiation = existing_ir
    else:
        irradiation = fetch_irradiation(date)
    print(f"  ☀️  Irradiation sum: {sum(irradiation):.0f} W/m²")

    # History + stats
    save_history(history, date, hourly_pv, irradiation, total_kwh, last_hour)
    stats = compute_stats(history)
    print(f"  📈 30-day avg: {stats['daily_avg']} kWh | Days: {stats['sample_days']}")

    # Status + alerts
    status = determine_status(total_kwh, hourly_pv, stats, last_hour)
    print(f"  🔔 Status: {status.upper()}")

    prev_status = "ok"
    if ALERT_FILE.exists():
        try: prev_status = json.loads(ALERT_FILE.read_text()).get("status", "ok")
        except Exception: pass
    if status != prev_status:
        emoji = "✅" if status == "ok" else "⚠️" if status == "low" else "❌"
        send_telegram(f"{emoji} <b>{SITE_NAME}</b> is now <b>{status.upper()}</b>\n{total_kwh} kWh today")
        print(f"  📱 Alert sent: {prev_status} → {status}")
    ALERT_FILE.write_text(json.dumps({"status": status}))

    # Write processed.json
    output = {
        "plant": SITE_NAME, "date": date,
        "last_updated": data.get("fetched_at", datetime.now(SAST).isoformat()),
        "total_kwh": total_kwh, "last_hour": last_hour,
        "status": status,
        "alerts": {"offline": total_kwh <= 0, "pace_low": status == "low", "total_low": False},
        "today": {"hourly_pv": hourly_pv, "irradiation": irradiation},
        "hourly_pv": hourly_pv, "irradiation": irradiation,
        "stats_30day": stats, "history": history,
    }
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"  ✅ Saved: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
