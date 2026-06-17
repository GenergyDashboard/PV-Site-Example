"""
scrape.py — Single-site FusionSolar Playwright scraper

Opens a hidden browser, logs into the FusionSolar portal, searches for the
plant, downloads the XLSX report, and saves it to data/raw/.

This is the SCRAPER approach (browser automation).
fetch.py is the API approach (HTTP requests only, faster).
Both produce data that process.py can read.

Environment variables (GitHub Secrets):
    FUSIONSOLAR_USERNAME - Portal login email
    FUSIONSOLAR_PASSWORD - Portal login password
    (these are DIFFERENT from the API credentials used by fetch.py)
"""

import os, sys, time, random, socket, subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── SITE CONFIG ──────────────────────────────────────────────
SITE_NAME = "Addo Spar Smart Logger"   # Exact name in FusionSolar portal
SITE_SLUG = "addo-spar"                # Output filename

# ── PATHS & CONFIG ───────────────────────────────────────────
RAW_DIR = Path(__file__).parent / "data" / "raw"
LOGIN_URL = "https://intl.fusionsolar.huawei.com/pvmswebsite/login/build/index.html"
PORTAL_HOME = "https://intl.fusionsolar.huawei.com/pvmswebsite/nologin/assets/build/index.html"
FALLBACK_IP = "119.8.160.213"
HOST = "intl.fusionsolar.huawei.com"


def human_delay(min_s=1.5, max_s=3):
    time.sleep(random.uniform(min_s, max_s))


def fix_dns():
    """Fix DNS resolution for FusionSolar on GitHub runners."""
    print(f"🔍 Checking DNS for {HOST}...")
    try:
        socket.gethostbyname(HOST)
        print(f"  ✅ DNS OK")
        return
    except socket.gaierror:
        print(f"  ⚠️  DNS failed, using fallback...")

    resolved_ip = FALLBACK_IP
    try:
        result = subprocess.run(
            ["dig", "+short", HOST, "@8.8.8.8"],
            capture_output=True, text=True, timeout=10)
        ips = [l.strip() for l in result.stdout.strip().split("\n")
               if l.strip() and not l.strip().endswith(".")]
        if ips:
            resolved_ip = ips[0]
    except Exception:
        pass

    print(f"  Using IP: {resolved_ip}")
    hosts_entry = f"{resolved_ip} {HOST}\n"
    try:
        subprocess.run(["sudo", "tee", "-a", "/etc/hosts"],
                       input=hosts_entry, capture_output=True, text=True, timeout=5)
    except Exception:
        with open("/etc/hosts", "a") as f:
            f.write(hosts_entry)
    print(f"  ✅ Added to /etc/hosts")


def dismiss_modals(page):
    """Close any popups/modals that might block interaction."""
    try:
        page.keyboard.press("Escape")
        human_delay(0.5, 1)
    except Exception:
        pass


def main():
    username = os.environ.get("FUSIONSOLAR_USERNAME", "")
    password = os.environ.get("FUSIONSOLAR_PASSWORD", "")
    if not username or not password:
        print("❌ Set FUSIONSOLAR_USERNAME and FUSIONSOLAR_PASSWORD")
        sys.exit(1)

    print(f"🚀 FusionSolar Scraper — {SITE_NAME}")
    print(f"🔐 Username: {username[:4]}***")

    fix_dns()

    from playwright.sync_api import sync_playwright

    output_file = RAW_DIR / f"{SITE_SLUG}.xlsx"
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        try:
            # ── Login ────────────────────────────────────────
            print("📱 Navigating to login page...")
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
            human_delay(3, 5)

            print("👤 Entering credentials...")
            page.get_by_role("textbox", name="Username or email").fill(username)
            human_delay(0.5, 1)
            page.get_by_role("textbox", name="Password").fill(password)
            human_delay(0.5, 1)
            page.get_by_text("Log In").click()
            page.wait_for_load_state("networkidle", timeout=60000)
            human_delay(5, 8)

            print(f"  📍 URL: {page.url[:80]}")
            if "login" in page.url.lower():
                print("  ⚠️  Still on login page, waiting...")
                human_delay(8, 12)
                if "login" in page.url.lower():
                    raise RuntimeError("Login failed — still on login page")
            print("  ✅ Login successful")

            # ── Search for plant ─────────────────────────────
            print(f"\n  ── Downloading: {SITE_NAME} ──")
            page.goto(PORTAL_HOME, wait_until="domcontentloaded", timeout=60000)
            human_delay(2, 4)
            dismiss_modals(page)

            print(f"  🔎 Searching for '{SITE_NAME}'...")
            search_field = None
            for sel in [
                page.get_by_role("textbox", name="Plant name"),
                page.locator("input[placeholder*='plant']").first,
                page.locator("input[placeholder*='Plant']").first,
                page.locator("input[placeholder*='search']").first,
            ]:
                try:
                    if sel.is_visible(timeout=3000):
                        search_field = sel
                        break
                except Exception:
                    continue

            if not search_field:
                raise RuntimeError("Could not find search field")

            search_field.click()
            human_delay(0.5, 1)
            search_field.fill(SITE_NAME)
            human_delay(1, 2)

            try:
                page.get_by_role("button", name="Search").click()
            except Exception:
                search_field.press("Enter")

            page.wait_for_load_state("networkidle", timeout=30000)
            human_delay(3, 5)

            # ── Click plant ──────────────────────────────────
            print(f"  🏢 Selecting '{SITE_NAME}'...")
            try:
                page.get_by_role("link", name=SITE_NAME).click()
            except Exception:
                page.get_by_text(SITE_NAME).first.click()

            page.wait_for_load_state("networkidle", timeout=60000)
            human_delay(3, 5)

            # ── Report Management ────────────────────────────
            print("  📊 Opening Report Management...")
            page.get_by_text("Report Management").click()
            page.wait_for_load_state("networkidle", timeout=60000)
            human_delay(3, 5)

            # ── Export & Download ─────────────────────────────
            print("  📤 Clicking Export...")
            page.get_by_role("button", name="Export").click()
            human_delay(3, 5)

            print("  💾 Downloading...")
            with page.expect_download(timeout=30000) as dl_info:
                page.get_by_title("Download").first.click()
            download = dl_info.value
            download.save_as(output_file)

            print(f"  ✅ Saved: {output_file}")

        except Exception as e:
            print(f"\n❌ Scraper failed: {e}")
            # Save debug screenshot
            try:
                page.screenshot(path="error_screenshot.png")
                print("  📸 Saved error_screenshot.png")
            except Exception:
                pass
            sys.exit(1)

        finally:
            browser.close()
            print("🔒 Browser closed")


if __name__ == "__main__":
    main()
