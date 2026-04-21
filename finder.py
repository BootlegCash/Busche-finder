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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def warmup(base_url: str) -> None:
    """Visit a site's homepage to pick up session cookies before scraping."""
    try:
        SESSION.get(base_url, timeout=10)
        time.sleep(1)
    except Exception:
        pass

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


def maps_link(address: str) -> str:
    """Return a Google Maps URL for an address."""
    return f"https://maps.google.com/?q={quote_plus(address)}"


def notify(title: str, body: str, click_url: str = "") -> None:
    """Send a push notification via ntfy.sh — completely free, no account needed."""
    try:
        headers = {
            "Title": title,
            "Priority": "high",
            "Tags": "beer,tada,white_check_mark",
        }
        if click_url:
            headers["Click"] = click_url  # tapping the notification opens this URL
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers=headers,
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
    r = SESSION.get(url, timeout=15, headers={"Referer": "https://www.totalwine.com/"})

    if r.status_code != 200:
        log(f"    Total Wine returned HTTP {r.status_code} — skipping")
        return False

    # Prefer structured Next.js data
    nd = parse_nextjs_json(r.text)
    if nd:
        page_str = json.dumps(nd)
        if contains_product(page_str):
            if likely_in_stock(page_str):
                return True
            log(f"    Product found but marked out of stock")
            return False

    # Fallback: raw HTML
    if contains_product(r.text):
        if likely_in_stock(r.text):
            return True
        log(f"    Product found in HTML but marked out of stock")
        return False

    log(f"    Not found in search results")
    return False


def check_totalwine() -> list:
    warmup("https://www.totalwine.com/")
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
    r = SESSION.get(url, timeout=15, headers={"Referer": "https://www.walmart.com/"})

    if r.status_code != 200:
        log(f"    Walmart returned HTTP {r.status_code} — skipping")
        return False

    # Parse Next.js structured data first
    nd = parse_nextjs_json(r.text)
    if nd:
        page_str = json.dumps(nd)
        if contains_product(page_str):
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
            if likely_in_stock(page_str):
                return True
            log(f"    Product found but out of stock")
            return False

    if contains_product(r.text):
        if likely_in_stock(r.text):
            return True
        log(f"    Product found in HTML but out of stock")
        return False

    log(f"    Not found in search results")
    return False


def check_walmart() -> list:
    warmup("https://www.walmart.com/")
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
        "name": "BevMo! – Tucson Broadway",
        "address": "6228 E Broadway Blvd, Tucson AZ 85711",
        "lat": 32.2185, "lon": -110.8556,
    },
]


def check_bevmo() -> list:
    """Check BevMo site-wide search; flag any nearby stores if product appears."""
    found = []
    warmup("https://www.bevmo.com/")
    try:
        url = f"https://www.bevmo.com/search?q={quote_plus(PRODUCT)}"
        r = SESSION.get(url, timeout=15, headers={"Referer": "https://www.bevmo.com/"})

        if r.status_code != 200:
            log(f"  BevMo returned HTTP {r.status_code}")
            return found

        if not contains_product(r.text):
            log("  BevMo: not found site-wide")
            return found

        for s in _BEVMO_STORES:
            dist = haversine(HOME_LAT, HOME_LON, s["lat"], s["lon"])
            if dist <= RADIUS_MILES:
                log(f"  *** FOUND at {s['name']} ({dist:.1f} mi) ***")
                found.append({**s, "distance": dist, "source": "BevMo"})
    except Exception as e:
        log(f"  BevMo error: {e}")
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Busch Light official "Where to Buy" locator (powered by Locally.com)
# ─────────────────────────────────────────────────────────────────────────────

def _get_locally_company_id() -> str:
    """Scrape the Busch Light site to find their Locally.com company ID."""
    try:
        r = SESSION.get("https://www.buschlight.com/", timeout=15)
        m = re.search(r'company[_-]?id["\s:=\']+(\d+)', r.text, re.I)
        if m:
            return m.group(1)
        m = re.search(r'locally\.com[^"\']*company_id=(\d+)', r.text, re.I)
        if m:
            return m.group(1)
    except Exception:
        pass
    # Anheuser-Busch's known Locally.com company ID
    return "14"


def check_buschlight_locator() -> list:
    """
    Query the Busch Light official store locator.
    Their site embeds a Locally.com widget; we call that API directly.
    This is the most accurate source because it's the brand's own data.
    """
    found = []
    try:
        company_id = _get_locally_company_id()
        url = (
            "https://www.locally.com/stores/map_data"
            f"?company_id={company_id}"
            f"&locale={HOME_ZIP}"
            f"&radius={RADIUS_MILES}"
            f"&q={quote_plus(PRODUCT)}"
            "&no_variants=0"
        )
        r = SESSION.get(url, timeout=15, headers={
            "Referer": "https://www.buschlight.com/",
            "Accept": "application/json, text/javascript, */*",
        })

        if r.status_code != 200:
            log(f"  Busch Light locator returned HTTP {r.status_code}")
            return found

        data = r.json()
        stores = data.get("stores", data.get("results", []))
        for s in stores:
            lat = s.get("lat") or s.get("latitude")
            lon = s.get("lng") or s.get("longitude") or s.get("lon")
            if not (lat and lon):
                continue
            dist = haversine(HOME_LAT, HOME_LON, float(lat), float(lon))
            if dist > RADIUS_MILES:
                continue
            city  = s.get("city", "")
            state = s.get("state", "")
            zipcd = s.get("zip", "")
            addr  = f"{s.get('address', '')}, {city}, {state} {zipcd}".strip(", ")
            log(f"  *** FOUND at {s.get('name', 'Store')} ({dist:.1f} mi) ***")
            found.append({
                "name": s.get("name", "Unknown Store"),
                "address": addr,
                "lat": float(lat), "lon": float(lon),
                "distance": dist, "source": "Busch Light Locator",
            })

        if not found:
            log("  Not found via Busch Light locator")
    except Exception as e:
        log(f"  Busch Light locator error: {e}")
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Local Tucson liquor stores
# ─────────────────────────────────────────────────────────────────────────────

_LOCAL_STORES = [
    # Safeway — sells Busch products, very close to home
    {
        "name": "Safeway – 22nd St",
        "address": "6230 E 22nd St, Tucson AZ 85711",
        "lat": 32.2052, "lon": -110.8554,
        "search_url": "https://www.safeway.com/shop/search-results.html?q=busch+light+apple",
        "referer": "https://www.safeway.com/",
    },
    # Walgreens — sells beer/wine in AZ
    {
        "name": "Walgreens – Broadway",
        "address": "5885 E Broadway Blvd, Tucson AZ 85711",
        "lat": 32.2190, "lon": -110.8700,
        "search_url": "https://www.walgreens.com/search/results.jsp?Ntt=busch+light+apple",
        "referer": "https://www.walgreens.com/",
    },
    # Circle K — sells beer in AZ, multiple nearby
    {
        "name": "Circle K – Wilmot/Broadway",
        "address": "6101 E Broadway Blvd, Tucson AZ 85711",
        "lat": 32.2189, "lon": -110.8627,
        "search_url": None,  # no search page; availability checked by brand locator
        "referer": None,
    },
]


def check_local_store(s: dict) -> bool:
    """Check a local store's website for the product."""
    if not s.get("search_url"):
        return False
    try:
        r = SESSION.get(
            s["search_url"], timeout=15,
            headers={"Referer": s.get("referer", "")},
        )
        if r.status_code != 200:
            log(f"    HTTP {r.status_code}")
            return False
        if contains_product(r.text) and likely_in_stock(r.text):
            return True
        log(f"    Not found in search results")
    except Exception as e:
        log(f"    Error: {e}")
    return False


def check_local_stores() -> list:
    found = []
    for s in _LOCAL_STORES:
        dist = haversine(HOME_LAT, HOME_LON, s["lat"], s["lon"])
        if dist > RADIUS_MILES:
            continue
        log(f"  Checking {s['name']} ({dist:.1f} mi)…")
        if check_local_store(s):
            log(f"  *** FOUND at {s['name']}! ***")
            found.append({**s, "distance": dist, "source": "Local Store"})
        time.sleep(1.5)
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def run_check() -> list:
    log(f"Checking for '{PRODUCT}' within {RADIUS_MILES} miles…")
    all_found = []

    log("▶ Busch Light Official Locator")
    all_found += check_buschlight_locator()

    log("▶ Total Wine")
    all_found += check_totalwine()

    log("▶ Walmart")
    all_found += check_walmart()

    log("▶ BevMo")
    all_found += check_bevmo()

    log("▶ Local Stores (Safeway, Walgreens, Circle K)")
    all_found += check_local_stores()

    if all_found:
        lines = "\n\n".join(
            f"• {s['name']}  ({s['distance']:.1f} mi)\n"
            f"  {s['address']}\n"
            f"  Maps: {maps_link(s['address'])}"
            for s in all_found
        )
        # If only one store found, tapping the notification opens Maps directly
        click = maps_link(all_found[0]["address"]) if len(all_found) == 1 else ""
        notify(
            f"Busch Light Apple Found! ({len(all_found)} store{'s' if len(all_found) > 1 else ''})",
            f"Found near 4432 E El Sol Cir, Tucson:\n\n{lines}",
            click_url=click,
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
║  Stores:    Busch Locator·Total Wine·Walmart·BevMo·Local ║
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
