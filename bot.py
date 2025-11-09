#!/usr/bin/env python3
"""
steam-price-bot - tracks Steam market prices for items (items.txt OR inventory)
Sends Discord alerts (DISCORD_WEBHOOK) on >=10% price moves.
Designed for GitHub Actions usage.
"""

import os
import time
import json
import requests
import urllib.parse
from datetime import datetime, timezone  # ✅ FIXED (timezone-aware)

# === CONFIG ===
CURRENCY = 1             # 1 = USD; change if needed
PRICE_CACHE = "prices.json"
HISTORY_CSV = "history.csv"
ITEMS_FILE = "items.txt"
STEAM_ID_ENV = "STEAM_ID64"      # secret name on GitHub
DISCORD_WEBHOOK_ENV = "DISCORD_WEBHOOK"
DELAY_SEC = 1.2          # delay between market requests (safe)
RETRY_BASE = 2           # base seconds for 429 exponential backoff
MIN_ALERT_PCT = 0.10     # 10% alert threshold (positive or negative)
# ==============

STEAM_ID = os.getenv(STEAM_ID_ENV)
DISCORD_WEBHOOK = os.getenv(DISCORD_WEBHOOK_ENV)

if not DISCORD_WEBHOOK:
    print("ERROR: DISCORD_WEBHOOK not set in environment. Exiting.")
    exit(1)

# ---------- helpers ----------
def safe_get_json(url, max_retries=6):
    """GET JSON with simple retry + exponential backoff on 429/5xx"""
    delay = 1
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, timeout=30)
        except Exception as e:
            print(f"Request exception: {e}. Retry {attempt}/{max_retries} in {delay}s.")
            time.sleep(delay)
            delay *= 2
            continue

        if r.status_code == 200:
            try:
                return r.json()
            except Exception as e:
                print("Failed to parse JSON:", e)
                return None

        if r.status_code == 429:
            wait = RETRY_BASE ** attempt
            print(f"Rate limited (429). Sleeping {wait}s (attempt {attempt}).")
            time.sleep(wait)
            continue

        if 500 <= r.status_code < 600:
            print(f"Server error {r.status_code}. Sleep {delay}s (attempt {attempt}).")
            time.sleep(delay)
            delay *= 2
            continue

        print(f"HTTP {r.status_code} for {url}")
        return None

    print("Exceeded retries for", url)
    return None

def send_discord_message(text):
    payload = {"content": text}
    try:
        requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
    except Exception as e:
        print("Failed to post to Discord:", e)

def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def append_history(item, price):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    safe_item = item.replace('"', "'")   # ✅ Fix embedded quotes safely
    
    line = f'"{ts}","{safe_item}",{price}\n'

    if not os.path.exists(HISTORY_CSV):
        with open(HISTORY_CSV, "w", encoding="utf-8") as f:
            f.write('"timestamp","item","price"\n')

    with open(HISTORY_CSV, "a", encoding="utf-8") as f:
        f.write(line)

# ---------- inventory or items.txt ----------
def load_items_list():
    # 1) if items.txt exists and non-empty, use it
    if os.path.exists(ITEMS_FILE):
        with open(ITEMS_FILE, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines:
                print(f"Loaded {len(lines)} items from {ITEMS_FILE}")
                return list(dict.fromkeys(lines))  # unique preserve order

    # 2) fallback to Steam inventory (requires STEAM_ID64 secret)
    if not STEAM_ID:
        print("No items.txt and STEAM_ID64 not set. Nothing to do.")
        return []

    url = f"https://steamcommunity.com/inventory/{STEAM_ID}/730/2?l=english&count=2000"
    data = safe_get_json(url)
    if not data:
        print("Failed to fetch inventory.")
        return []

    descriptions = data.get("descriptions", [])
    marketable = []
    for d in descriptions:
        if d.get("marketable", 0) == 1 and "market_hash_name" in d:
            marketable.append(d["market_hash_name"])

    unique = list(dict.fromkeys(marketable))
    print(f"Loaded {len(unique)} marketable items from Steam inventory.")
    return unique

# ---------- price fetch ----------
def fetch_price_for_item(item_name):
    q = urllib.parse.quote(item_name, safe='')
    url = (
        "https://steamcommunity.com/market/priceoverview/"
        f"?appid=730&currency={CURRENCY}&market_hash_name={q}"
    )
    data = safe_get_json(url)
    if not data:
        return None

    price_str = data.get("lowest_price") or data.get("median_price")
    if not price_str:
        return None

    cleaned = (
        price_str.replace("$", "")
        .replace("USD", "")
        .replace(",", "")
        .strip()
    )

    try:
        return float(cleaned)
    except:
        return None

# ---------- main ----------
def main():
    prices = load_json(PRICE_CACHE)
    items = load_items_list()
    if not items:
        print("No items to check. Exiting.")
        return

    for idx, item in enumerate(items, start=1):
        print(f"[{idx}/{len(items)}] Checking: {item}")

        entry = prices.get(item)
        now = time.time()
        last_update = entry.get("last_update", 0) if isinstance(entry, dict) else 0

        hours_since = (now - last_update) / 3600 if last_update else None
        if last_update and hours_since < 12:
            print(f"  Skipping (updated {hours_since:.1f} h ago)")
            continue

        price = fetch_price_for_item(item)
        time.sleep(DELAY_SEC)

        if price is None:
            print("  Price fetch failed or no market data.")
            continue

        print(f"  Price: ${price:.2f}")

        append_history(item, price)

        if item not in prices:
            prices[item] = {"price": price, "last_update": now}
            save_json(PRICE_CACHE, prices)
            print("  First observation — saved.")
            continue

        old = prices[item].get("price", price)
        if old == 0:
            old = price

        change = (price - old) / old
        pct = round(change * 100, 2)
        print(f"  Change: {pct}% (old ${old:.2f})")

        if abs(change) >= MIN_ALERT_PCT:
            direction = "▲" if change > 0 else "▼"
            msg = (
                f"{direction} **Price Alert**\n"
                f"Item: `{item}`\n"
                f"Old: ${old:.2f}\n"
                f"New: ${price:.2f}\n"
                f"Change: **{pct}%**"
            )
            send_discord_message(msg)
            print("  Alert sent.")

        prices[item] = {"price": price, "last_update": now}
        save_json(PRICE_CACHE, prices)

    print("All done.")

if __name__ == "__main__":
    main()
