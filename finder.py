#!/usr/bin/env python3
"""
Busch Light Apple Finder
Scrapes Total Wine, Walmart, and BevMo for Busch Light Apple
within 10 miles of 4432 E El Sol Cir, Tucson AZ 85711.

FREE push notifications via ntfy.sh — no account needed.

SETUP:
  1. pip install -r requirements.txt
  2. python3 finder.py          # runs continuously, checks every hour
     python3 finder.py --once  # check once (good for cron jobs)

NOTIFICATIONS:
  Open https://ntfy.sh/busche-apple-tucson in your browser, OR
  Install the free "ntfy" app on your phone and subscribe to:
    Topic: busche-apple-tucson

CRON (run automatically every hour):
  crontab -e
  Add: 0 * * * * cd /home/user/Busche-finder && python3 finder.py --once >> /tmp/busche.log 2>&1
"""

import argparse
import json
import math
import re
import time
from datetime import datetime
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

HOME_LAT        = 32.2099        # 4432 E El Sol Cir, Tucson AZ 85711
HOME_LON        = -110.8735
HOME_ZIP        = "85711"
RADIUS_MILES    = 10
PRODUCT         = "Busch Light Apple"
NTFY_TOPIC      = "busche-apple-tucson"   # change if you want a private channel
CHECK_INTERVAL  = 3600                    # seconds between checks (1 hour)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in miles between two lat/lon points."""
    R = 3958.8
    rl1, rl2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rl1) * math.cos(rl2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def notify(title: str, body: str) -> None:
    """Send a push notification via ntfy.sh — completely free, no account needed."""
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "high",
                "Tags": "beer,tada,white_check_mark",
            },
            timeout=10,
        )
        log(f"[ntfy] Notification sent — HTTP {r.status_code}")
    except Exception as e:
        log(f"[ntfy] Failed to send: {e}")


def parse_nextjs_json(html: str) -> dict:
    """Extract the __NEXT_DATA__ JSON blob embedded in Next.js pages."""
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html, re.S
    )
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return {}


def contains_product(text: str) -> bool:
    """Return True if text mentions Busch Light Apple (case-insensitive)."""
    return bool(re.search(r"busch.{0,30}apple", text, re.I))


def likely_in_stock(text: str) -> bool:
    """Heuristic: text does NOT appear to say the item is out of stock."""
    out_signals = ["out of stock", "unavailable", "sold out", "currently unavailable"]
    text_lower = text.lower()
    if not any(sig in text_lower for sig in out_signals):
        return True
    # Even if OOS signals exist, an "add to cart" button means it's available
    return "add to cart" in text_lower


# ─────────────────────────────────────────────────────────────────────────────
# Total Wine scraper
# ─────────────────────────────────────────────────────────────────────────────

# Fallback store list in case the dynamic API is blocked
_TW_FALLBACK = [
    {
        "id": "224",
        "name": "Total Wine & More – Broadway",
        "address": "5870 E Broadway Blvd, Tucson AZ 85711",
        "lat": 32.2191, "lon": -110.8714,
    },
    {
        "id": "225",
        "name": "Total Wine & More – Oracle",
        "address": "7770 N Oracle Rd, Tucson AZ 85704",
        "lat": 32.3267, "lon": -110.9775,
    },
]


def _tw_stores_from_api() -> list:
    """Try to fetch Total Wine stores near home via their store-search API."""
    url = (
        "https://www.totalwine.com/api/store/search"
        f"?latitude={HOME_LAT}&longitude={HOME_LON}"
        f"&maxMiles={RADIUS_MILES}&pageSize=25"
    )
    r = SESSION.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    stores = []
    for s in data.get("stores", []):
        lat = s.get("latitude")
        lon = s.get("longitude")
        if lat and lon:
            dist = haversine(HOME_LAT, HOME_LON, float(lat), float(lon))
            if dist <= RADIUS_MILES:
                stores.append({
                    "id": str(s.get("storeNumber", "")),
                    "name": f"Total Wine – {s.get('storeName', 'Unknown')}",
                    "address": s.get("address", {}).get("address1", ""),
                    "lat": float(lat), "lon": float(lon),
                })
    return stores


def get_totalwine_stores() -> list:
    try:
        stores = _tw_stores_from_api()
        if stores:
            return stores
    except Exception as e:
        log(f"  [Total Wine] Store API failed ({e}), using fallback list")

    return [
        {**s, "distance": haversine(HOME_LAT, HOME_LON, s["lat"], s["lon"])}
        for s in _TW_FALLBACK
        if haversine(HOME_LAT, HOME_LON, s["lat"], s["lon"]) <= RADIUS_MILES
    ]


def check_totalwine_store(store: dict) -> bool:
    url = (
        "https://www.totalwine.com/search/all"
        f"?text={quote_plus(PRODUCT)}"
        "&producttype=Beer"
        f"&storeId={store['id']}"
        "&limitFrom=0&limitTo=24"
    )
    r = SESSION.get(url, timeout=15)

    # Prefer structured Next.js data
    nd = parse_nextjs_json(r.text)
    if nd:
        page_str = json.dumps(nd)
        if contains_product(page_str) and likely_in_stock(page_str):
            return True

    # Fallback: raw HTML
    if contains_product(r.text) and likely_in_stock(r.text):
        return True

    return False


def check_totalwine() -> list:
    stores = get_totalwine_stores()
    found = []
    for s in stores:
        dist = s.get("distance") or haversine(HOME_LAT, HOME_LON, s["lat"], s["lon"])
        log(f"  Checking {s['name']} ({dist:.1f} mi)…")
        try:
            if check_totalwine_store(s):
                log(f"  *** FOUND at {s['name']}! ***")
                found.append({**s, "distance": dist, "source": "Total Wine"})
        except Exception as e:
            log(f"  Error: {e}")
        time.sleep(2)
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Walmart scraper
# ─────────────────────────────────────────────────────────────────────────────

_WM_FALLBACK = [
    {
        "id": "3455",
        "name": "Walmart Supercenter – Grant Rd",
        "address": "3971 E Grant Rd, Tucson AZ 85712",
        "lat": 32.2529, "lon": -110.8988,
    },
    {
        "id": "2101",
        "name": "Walmart Supercenter – Speedway",
        "address": "5405 E Speedway Blvd, Tucson AZ 85712",
        "lat": 32.2576, "lon": -110.8929,
    },
    {
        "id": "2186",
        "name": "Walmart – Irvington",
        "address": "3398 S Irvington Rd, Tucson AZ 85714",
        "lat": 32.1780, "lon": -110.9011,
    },
    {
        "id": "4558",
        "name": "Walmart Supercenter – Old Vail",
        "address": "9150 E Old Vail Rd, Tucson AZ 85747",
        "lat": 32.1511, "lon": -110.7987,
    },
]


def _wm_stores_from_api() -> list:
    """Try to fetch nearby Walmart stores via their store-finder endpoint."""
    url = (
        f"https://www.walmart.com/store/finder/update"
        f"?location={HOME_ZIP}&distance={RADIUS_MILES}"
    )
    r = SESSION.get(url, timeout=15, headers={**HEADERS, "Accept": "application/json"})
    r.raise_for_status()
    data = r.json()
    stores = []
    for s in (data.get("payload", {})
                   .get("storesData", {})
                   .get("stores", [])):
        geo = s.get("geoPoint", {})
        lat, lon = geo.get("latitude"), geo.get("longitude")
        if lat and lon:
            dist = haversine(HOME_LAT, HOME_LON, float(lat), float(lon))
            if dist <= RADIUS_MILES:
                stores.append({
                    "id": str(s.get("id", "")),
                    "name": f"Walmart #{s.get('id')} – {s.get('displayName', '')}",
                    "address": s.get("address", {}).get("address", ""),
                    "lat": float(lat), "lon": float(lon),
                })
    return stores


def get_walmart_stores() -> list:
    try:
        stores = _wm_stores_from_api()
        if stores:
            return stores
    except Exception as e:
        log(f"  [Walmart] Store API failed ({e}), using fallback list")

    return [
        {**s, "distance": haversine(HOME_LAT, HOME_LON, s["lat"], s["lon"])}
        for s in _WM_FALLBACK
        if haversine(HOME_LAT, HOME_LON, s["lat"], s["lon"]) <= RADIUS_MILES
    ]


def check_walmart_store(store: dict) -> bool:
    url = f"https://www.walmart.com/search?q={quote_plus(PRODUCT)}&stores={store['id']}"
    r = SESSION.get(url, timeout=15)

    # Parse Next.js structured data first
    nd = parse_nextjs_json(r.text)
    if nd:
        page_str = json.dumps(nd)
        if contains_product(page_str):
            # Walk item stacks for explicit availability
            stacks = (nd.get("props", {})
                        .get("pageProps", {})
                        .get("initialData", {})
                        .get("searchResult", {})
                        .get("itemStacks", []))
            for stack in stacks:
                for item in stack.get("items", []):
                    if contains_product(item.get("name", "")):
                        avail = (item.get("availabilityStatusV2", {})
                                     .get("display", ""))
                        if avail.lower() not in ("out of stock", "unavailable"):
                            return True
            # If no explicit availability data but product is mentioned
            if likely_in_stock(page_str):
                return True

    # Fallback: raw HTML
    if contains_product(r.text) and likely_in_stock(r.text):
        return True

    return False


def check_walmart() -> list:
    stores = get_walmart_stores()
    found = []
    for s in stores:
        dist = s.get("distance") or haversine(HOME_LAT, HOME_LON, s["lat"], s["lon"])
        log(f"  Checking {s['name']} ({dist:.1f} mi)…")
        try:
            if check_walmart_store(s):
                log(f"  *** FOUND at {s['name']}! ***")
                found.append({**s, "distance": dist, "source": "Walmart"})
        except Exception as e:
            log(f"  Error: {e}")
        time.sleep(2)
    return found


# ─────────────────────────────────────────────────────────────────────────────
# BevMo scraper
# ─────────────────────────────────────────────────────────────────────────────

_BEVMO_STORES = [
    {
        "name": "BevMo! – Tucson (Calle Santa Cruz)",
        "address": "5425 S Calle Santa Cruz, Tucson AZ 85706",
        "lat": 32.1578, "lon": -110.9653,
    },
]


def check_bevmo() -> list:
    """Check BevMo site-wide search; flag any nearby stores if product appears."""
    found = []
    try:
        url = f"https://www.bevmo.com/search?q={quote_plus(PRODUCT)}"
        r = SESSION.get(url, timeout=15)

        if not contains_product(r.text):
            log("  BevMo: not found site-wide")
            return found

        for s in _BEVMO_STORES:
            dist = haversine(HOME_LAT, HOME_LON, s["lat"], s["lon"])
            if dist <= RADIUS_MILES:
                log(f"  *** FOUND at {s['name']} ({dist:.1f} mi) — verify in-stock ***")
                found.append({**s, "distance": dist, "source": "BevMo"})
    except Exception as e:
        log(f"  BevMo error: {e}")
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Fry's Food (Kroger) scraper
# ─────────────────────────────────────────────────────────────────────────────

_FRYS_STORES = [
    {
        "id": "70200113",
        "name": "Fry's Food – Speedway/Wilmot",
        "address": "5765 E Speedway Blvd, Tucson AZ 85712",
        "lat": 32.2578, "lon": -110.8688,
    },
    {
        "id": "70200114",
        "name": "Fry's Food – Grant/Craycroft",
        "address": "4811 E Grant Rd, Tucson AZ 85712",
        "lat": 32.2526, "lon": -110.8828,
    },
    {
        "id": "70200133",
        "name": "Fry's Food – Broadway/Craycroft",
        "address": "6002 E Broadway Blvd, Tucson AZ 85711",
        "lat": 32.2188, "lon": -110.8665,
    },
]


def check_frys_store(store: dict) -> bool:
    """Search Fry's (Kroger) product page for Busch Light Apple."""
    url = (
        f"https://www.frysfood.com/search?query={quote_plus(PRODUCT)}"
        f"&searchType=natural&fulfillment=PICKUP&storeId={store['id']}"
    )
    r = SESSION.get(url, timeout=15)
    if contains_product(r.text) and likely_in_stock(r.text):
        # Try JSON embedded data
        nd = parse_nextjs_json(r.text)
        if nd:
            page_str = json.dumps(nd)
            return contains_product(page_str) and likely_in_stock(page_str)
        return True
    return False


def check_frys() -> list:
    found = []
    for s in _FRYS_STORES:
        dist = haversine(HOME_LAT, HOME_LON, s["lat"], s["lon"])
        if dist > RADIUS_MILES:
            continue
        log(f"  Checking {s['name']} ({dist:.1f} mi)…")
        try:
            if check_frys_store(s):
                log(f"  *** FOUND at {s['name']}! ***")
                found.append({**s, "distance": dist, "source": "Fry's Food"})
        except Exception as e:
            log(f"  Error: {e}")
        time.sleep(2)
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def run_check() -> list:
    log(f"Checking for '{PRODUCT}' within {RADIUS_MILES} miles…")
    all_found = []

    log("▶ Total Wine")
    all_found += check_totalwine()

    log("▶ Walmart")
    all_found += check_walmart()

    log("▶ BevMo")
    all_found += check_bevmo()

    log("▶ Fry's Food")
    all_found += check_frys()

    if all_found:
        lines = "\n\n".join(
            f"• {s['name']}  ({s['distance']:.1f} mi)\n  {s['address']}\n  Source: {s['source']}"
            for s in all_found
        )
        notify(
            f"Busch Light Apple Found! ({len(all_found)} store{'s' if len(all_found) > 1 else ''})",
            f"Found near 4432 E El Sol Cir, Tucson:\n\n{lines}",
        )
    else:
        log("Not found in any stores this round.")

    log("Check complete.")
    return all_found


def main() -> None:
    parser = argparse.ArgumentParser(description="Busch Light Apple Finder")
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single check and exit (use this with cron)"
    )
    args = parser.parse_args()

    banner = f"""
╔══════════════════════════════════════════════════════════╗
║            Busch Light Apple Finder                      ║
║  Searching: {RADIUS_MILES} miles from 4432 E El Sol Cir, Tucson AZ  ║
║  Stores:    Total Wine · Walmart · BevMo · Fry's         ║
║  Notify:    https://ntfy.sh/{NTFY_TOPIC:<28}║
╚══════════════════════════════════════════════════════════╝
To receive alerts on your phone:
  1. Install the FREE "ntfy" app (iOS or Android)
  2. Subscribe to topic: {NTFY_TOPIC}
  OR open https://ntfy.sh/{NTFY_TOPIC} in any browser
"""
    print(banner)

    if args.once:
        run_check()
    else:
        while True:
            run_check()
            log(f"Sleeping {CHECK_INTERVAL // 60} min until next check. Ctrl+C to stop.")
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
