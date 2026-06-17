"""
fetch.py — Single-site FusionSolar API fetcher

Logs into the Northbound API, fetches today's hourly data for one site,
and saves it as JSON. No browser, no scraping — just HTTP requests.

Environment variables (GitHub Secrets):
    FUSIONSOLAR_API_USER - Northbound API username
    FUSIONSOLAR_API_PASS - Northbound API password

Run normally:     python fetch.py
Discover sites:   DISCOVER=1 python fetch.py
"""

import json, os, sys, time, socket, subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ── SITE CONFIG (change these for your site) ─────────────────
SITE_NAME    = "Addo Spar Smart Logger"   # Exact name in FusionSolar
SITE_SLUG    = "addo-spar"                # Folder name (lowercase, hyphens)
STATION_CODE = "NE=51009860"              # From discovery (DISCOVER=1)
LAT          = -33.4710                   # GPS latitude
LON          = 25.7530                    # GPS longitude

# ── API CONFIG ───────────────────────────────────────────────
API_BASE = "https://intl.fusionsolar.huawei.com/thirdData"
SAST = timezone(timedelta(hours=2))
RAW_DIR = Path(__file__).parent / "data" / "raw"
FALLBACK_IP = "119.8.160.213"
HOST = "intl.fusionsolar.huawei.com"


# ── DNS FIX (GitHub runners can't resolve FusionSolar) ───────
def fix_dns():
    print(f"🔍 Checking DNS for {HOST}...")
    try:
        ip = socket.gethostbyname(HOST)
        print(f"  ✅ DNS OK: {HOST} → {ip}")
        return
    except socket.gaierror:
        print(f"  ⚠️  DNS failed, trying Google DNS...")

    resolved_ip = None
    try:
        result = subprocess.run(
            ["dig", "+short", HOST, "@8.8.8.8"],
            capture_output=True, text=True, timeout=10,
        )
        ips = [l.strip() for l in result.stdout.strip().split("\n")
               if l.strip() and not l.strip().endswith(".")]
        if ips:
            resolved_ip = ips[0]
    except Exception:
        pass

    resolved_ip = resolved_ip or FALLBACK_IP
    print(f"  Using IP: {resolved_ip}")

    hosts_entry = f"{resolved_ip} {HOST}\n"
    try:
        result = subprocess.run(
            ["sudo", "tee", "-a", "/etc/hosts"],
            input=hosts_entry, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            with open("/etc/hosts", "a") as f:
                f.write(hosts_entry)
    except Exception:
        with open("/etc/hosts", "a") as f:
            f.write(hosts_entry)

    print(f"  ✅ Added to /etc/hosts")


# ── API CLIENT ───────────────────────────────────────────────
class FusionSolarAPI:
    def __init__(self, username, password):
        self.session = requests.Session()
        self.username = username
        self.password = password

    def login(self):
        """Login and get XSRF-TOKEN cookie."""
        print("🔐 Logging in...")
        resp = self.session.post(
            f"{API_BASE}/login",
            json={"userName": self.username, "systemCode": self.password},
            timeout=30,
        )
        token = self.session.cookies.get("XSRF-TOKEN")
        if token:
            self.session.headers.update({"XSRF-TOKEN": token})
            print("  ✅ Login successful")
            return True
        try:
            data = resp.json()
            print(f"  ❌ Login failed: failCode={data.get('failCode')}")
        except Exception:
            print(f"  ❌ Login failed: HTTP {resp.status_code}")
        return False

    def logout(self):
        try:
            self.session.post(f"{API_BASE}/logout", timeout=10)
        except Exception:
            pass

    def call(self, endpoint, body, _retried=False):
        """Make an API call with auto-relogin on session expiry."""
        resp = self.session.post(f"{API_BASE}/{endpoint}", json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("success"):
            return data
        fail = data.get("failCode")
        if fail in (305, 401) and not _retried:
            print(f"  ⚠️  Session expired, re-logging in...")
            self.login()
            return self.call(endpoint, body, _retried=True)
        if fail in (407, 429) and not _retried:
            print(f"  ⚠️  Rate limited, waiting 60s...")
            time.sleep(60)
            return self.call(endpoint, body, _retried=True)
        raise RuntimeError(f"API error: {data}")


# ── MAIN ─────────────────────────────────────────────────────
def main():
    username = os.environ.get("FUSIONSOLAR_API_USER", "")
    password = os.environ.get("FUSIONSOLAR_API_PASS", "")
    if not username or not password:
        print("❌ Set FUSIONSOLAR_API_USER and FUSIONSOLAR_API_PASS")
        sys.exit(1)

    print(f"🚀 FusionSolar Fetcher — {SITE_NAME}")
    fix_dns()

    api = FusionSolarAPI(username, password)
    try:
        if not api.login():
            sys.exit(1)

        # Discovery mode
        if os.environ.get("DISCOVER") == "1":
            data = api.call("getStationList", {"pageNo": 1, "pageSize": 100})
            stations = data.get("data", {}).get("list", [])
            print(f"\n{len(stations)} stations found:\n")
            print(f"  {'NAME':<40} {'STATION CODE':<20} {'CAPACITY'}")
            for s in stations:
                print(f"  {s.get('stationName','?'):<40} {s.get('stationCode','?'):<20} {s.get('capacity',0)} kWp")
            return

        now = datetime.now(SAST)
        today_str = now.strftime("%Y-%m-%d")
        midnight = datetime.strptime(today_str, "%Y-%m-%d").replace(tzinfo=SAST)
        collect_time = int(midnight.timestamp() * 1000)

        # 1. Real-time KPI
        print(f"\n📊 Fetching real-time data...")
        rt_data = api.call("getStationRealKpi", {"stationCodes": STATION_CODE})
        rt = {}
        for item in rt_data.get("data", []):
            if item.get("stationCode") == STATION_CODE:
                rt = item.get("dataItemMap", {})
        print(f"  day_power: {rt.get('day_power', 0)} kWh")

        time.sleep(3)

        # 2. Hourly KPI
        print(f"📈 Fetching hourly data for {today_str}...")
        hr_data = api.call("getKpiStationHour", {
            "stationCodes": STATION_CODE,
            "collectTime": collect_time,
        })

        # Show available fields (for learning/debugging)
        entries = hr_data.get("data", [])
        if entries:
            sample = entries[0].get("dataItemMap", {})
            print(f"\n  📋 Available fields in dataItemMap:")
            for k, v in sorted(sample.items()):
                print(f"      {k:<30} = {v}")

        # Build hourly PV array
        hourly_pv = [0.0] * 24
        for entry in entries:
            dim = entry.get("dataItemMap", {})
            ct = entry.get("collectTime", 0)
            if ct:
                hour = datetime.fromtimestamp(ct / 1000, tz=SAST).hour
                # inverter_power = total PV output (kWh)
                pv_val = float(dim.get("inverter_power", 0) or 0)
                if pv_val == 0:
                    pv_val = float(dim.get("PVYield", 0) or 0)
                # Fallback: derive from consumption - grid import + export
                if pv_val == 0:
                    use = float(dim.get("use_power", 0) or 0)
                    buy = float(dim.get("buyPower", 0) or 0)
                    grid = float(dim.get("ongrid_power", 0) or 0)
                    if use > 0:
                        pv_val = max(0, use - buy) + grid
                hourly_pv[hour] += round(pv_val, 2)

        hourly_pv = [round(v, 2) for v in hourly_pv]
        total_kwh = round(float(rt.get("day_power", 0) or 0) or sum(hourly_pv), 2)
        last_hour = max((h for h in range(24) if hourly_pv[h] > 0), default=0)

        # Save raw JSON
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        output = {
            "slug": SITE_SLUG,
            "plant_name": SITE_NAME,
            "station_code": STATION_CODE,
            "date": today_str,
            "total_kwh": total_kwh,
            "day_power": rt.get("day_power", 0),
            "month_power": rt.get("month_power", 0),
            "total_power": rt.get("total_power", 0),
            "hourly_pv": hourly_pv,
            "last_hour": last_hour,
            "fetched_at": datetime.utcnow().isoformat(),
        }

        out_file = RAW_DIR / f"{SITE_SLUG}.json"
        with open(out_file, "w") as f:
            json.dump(output, f, indent=2)

        print(f"\n  ✅ {SITE_NAME}: {total_kwh} kWh")
        print(f"     Hourly: {sum(1 for v in hourly_pv if v > 0)} hours with data")
        print(f"     Saved: {out_file}")

    finally:
        api.logout()
        print("🔒 Done")


if __name__ == "__main__":
    main()
