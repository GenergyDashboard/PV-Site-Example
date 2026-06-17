"""
refresh_irradiation.py — Fixes missing irradiation data

Runs separately from the main fetch. If Open-Meteo was down during the
main fetch, this script catches it on the next run.
"""
import json, time, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

SITE_SLUG = "addo-spar"
LAT, LON = -33.4710, 25.7530
SAST = timezone(timedelta(hours=2))
SITE_DIR = Path(__file__).parent / "sites" / SITE_SLUG / "data"

def fetch_irradiation(date_str):
    for attempt in range(3):
        try:
            resp = requests.get("https://api.open-meteo.com/v1/forecast",
                params={"latitude": LAT, "longitude": LON,
                        "hourly": "shortwave_radiation",
                        "start_date": date_str, "end_date": date_str},
                timeout=15)
            resp.raise_for_status()
            irrad = resp.json().get("hourly", {}).get("shortwave_radiation", [])
            while len(irrad) < 24: irrad.append(0)
            result = [0.0] * 24
            for h in range(24):
                if h + 1 <= 23: result[h + 1] = round(irrad[h] or 0, 1)
            if sum(result) < 1: raise ValueError("Near zero")
            return result
        except Exception as e:
            if attempt < 2: time.sleep(3)
    return None

def main():
    today = datetime.now(SAST).strftime("%Y-%m-%d")
    print(f"☀️  Irradiation refresh for {SITE_SLUG} ({today})")
    proc_file = SITE_DIR / "processed.json"
    hist_file = SITE_DIR / "history.json"
    if proc_file.exists():
        proc = json.loads(proc_file.read_text())
        ir = proc.get("today", {}).get("irradiation", [])
        if sum(ir) > 10:
            print(f"  ✅ Already OK (sum={sum(ir):.0f})"); return
    irrad = fetch_irradiation(today)
    if not irrad:
        print("  ❌ Failed"); return
    print(f"  ☀️  Fetched sum={sum(irrad):.0f}")
    if proc_file.exists():
        proc = json.loads(proc_file.read_text())
        proc.setdefault("today", {})["irradiation"] = irrad
        proc["irradiation"] = irrad
        proc_file.write_text(json.dumps(proc, indent=2))
    if hist_file.exists():
        hist = json.loads(hist_file.read_text())
        if today in hist: hist[today]["irradiation"] = irrad
        hist_file.write_text(json.dumps(hist, indent=2))
    print("  ✅ Updated")

if __name__ == "__main__": main()
