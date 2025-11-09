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
from datetime import datetime, timezone  # ✅ timezone-aware
from tqdm import tqdm  # ✅ progress bar

# === CONFIG ===
CURRENCY = 24            # ✅ INR (Indian Rupees)
PRICE_CACHE = ".data/prices.json"
HISTORY_CSV = ".data/history.csv"
ITEMS_FILE = "items.txt"
STEAM_ID_ENV = "STEAM_ID64"      # secret name on GitHub
DISCORD_WEBHOOK_ENV = "DISCORD_WEBHOOK"
DELAY_SEC = 1.2
RETRY_BASE = 2
MIN_ALERT_PCT = 0.10
# ==============

STEAM_ID = os.getenv(STEAM_ID_ENV)
DISCORD_WEBHOOK = os.getenv(DISCORD_WEBHOOK_ENV)

if not DISCORD_WEBHOOK:
    print("ERROR: DISCORD_WEBHOOK not set in environment. Exiting.")
    exit(1)

# ---------- helpers ----------
def safe_get_json(url, max_retries=6):
    print(f"  [HTTP] GET {url}")
    delay = 1
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, timeout=30)
        except Exception as e:
            print(f"  [HTTP] Exception: {e} (retry {attempt}/{max_retries})")
            time.sleep(delay)
            delay *= 2
            continue

        if r.status_code == 200:
            print("  [HTTP] OK 200")
            try:
                return r.json()
            except Exception as e:
                print("  [HTTP] Failed to parse JSON:", e)
                return None

        if r.status_code == 429:
            wait = RETRY_BASE ** attempt
            print(f"  [HTTP] 429 RATE LIMITED → waiting {wait}s (retry {attempt}/{max_retries})")
            time.sleep(wait)
            continue

        if 500 <= r.status_code < 600:
            print(f"  [HTTP] Server error {r.status_code}, retrying in {delay}s")
            time.sleep(delay)
            delay *= 2
            continue

        print(f"  [HTTP] ERROR {r.status_code}")
        return None

    print("  [HTTP] FAILED: max retries reached")
    return None


def send_discord_message(text):
    print("  [Discord] Sending alert…")
    payload = {"content": text}
    try:
        requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        print("  [Discord] Alert sent ✅")
    except Exception as e:
        print("  [Discord] FAILED:", e)


def load_json(path):
    print(f"[Init] Loading JSON cache: {path}")
    if not os.path.exists(path):
        print("  No cache found – starting fresh.")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            print("  Cache loaded.")
            return json.load(f)
        except:
            print("  Cache invalid – starting empty.")
            return {}


def save_json(path, data):
    print(f"  [Save] Updating cache: {path}")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def append_history(item, price):
    print(f"  [History] Logging price for: {item}")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    safe_item = item.replace('"', "'")
    line = f'"{ts}","{safe_item}",{price}\n'

    if not os.path.exists(HISTORY_CSV):
        print("  [History] Creating history CSV file.")
        with open(HISTORY_CSV, "w", encoding="utf-8") as f:
            f.write('"timestamp","item","price"\n')

    with open(HISTORY_CSV, "a", encoding="utf-8") as f:
        f.write(line)


# ---------- inventory or items.txt ----------
def load_items_list():
    print("[Init] Loading items…")

    if os.path.exists(ITEMS_FILE):
        print(f"  Found items.txt → loading {ITEMS_FILE}")
        with open(ITEMS_FILE, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines:
                print(f"  Loaded {len(lines)} items from items.txt ✅")
                return list(dict.fromkeys(lines))

    if not STEAM_ID:
        print("  STEAM_ID not set → cannot fetch inventory.")
        return []

    print(f"  Fetching Steam inventory for {STEAM_ID}…")
    url = f"https://steamcommunity.com/inventory/{STEAM_ID}/730/2?l=english&count=2000"
    data = safe_get_json(url)
    if not data:
        print("  [ERROR] Failed to load Steam inventory.")
        return []

    descriptions = data.get("descriptions", [])
    marketable = [d["market_hash_name"] for d in descriptions if d.get("marketable", 0) == 1]

    unique = list(dict.fromkeys(marketable))
    print(f"  ✅ Loaded {len(unique)} marketable items from inventory.")
    return unique


# ---------- price fetch ----------
def fetch_price_for_item(item_name):
    print(f"    [Price] Fetching price for: {item_name}")
    q = urllib.parse.quote(item_name, safe='')
    url = f"https://steamcommunity.com/market/priceoverview/?appid=730&currency={CURRENCY}&market_hash_name={q}"
    data = safe_get_json(url)

    if not data:
        print("    [Price] ⚠️ Failed to fetch price")
        return None

    price_str = data.get("lowest_price") or data.get("median_price")
    if not price_str:
        print("    [Price] ⚠️ No price data")
        return None

    cleaned = price_str.replace("₹", "").replace("INR", "").replace(",", "").strip()

    try:
        val = float(cleaned)
        print(f"    [Price] ✅ Parsed price: ₹{val}")
        return val
    except:
        print("    [Price] ❌ Could not parse cleaned price:", cleaned)
        return None


# ---------- main ----------
def main():
    print("========== Steam Price Bot Starting ==========")

    prices = load_json(PRICE_CACHE)

    print("\n========== Loading Item List ==========")
    items = load_items_list()
    if not items:
        print("❌ No items to check. Exiting.")
        return

    print("\n========== Beginning Price Checks ==========")
    print(f"Total items to check: {len(items)}\n")

    # ✅ Progress bar here
    for idx, item in enumerate(tqdm(items, desc="Checking Prices", unit="item"), start=1):
        print(f"\n--- [{idx}/{len(items)}] Processing item: {item} ---")

        entry = prices.get(item)
        now = time.time()
        last_update = entry.get("last_update", 0) if isinstance(entry, dict) else 0

        hours_since = (now - last_update) / 3600 if last_update else None

        if last_update and hours_since < 1:
            print(f"  ⏩ Skipping (updated {hours_since:.1f} hours ago)")
            continue

        price = fetch_price_for_item(item)
        time.sleep(DELAY_SEC)

        if price is None:
            print("  ⚠️ Price fetch failed.")
            continue

        print(f"  ✅ Current price: ₹{price:.2f}")

        append_history(item, price)

        if item not in prices:
            print("  🆕 First-time price — saving.")
            prices[item] = {"price": price, "last_update": now}
            save_json(PRICE_CACHE, prices)
            continue

        old = prices[item].get("price", price)
        if old == 0:
            old = price

        change = (price - old) / old
        pct = round(change * 100, 2)

        print(f"  Price change: {pct}% (old ₹{old:.2f})")

        if abs(change) >= MIN_ALERT_PCT:
            print("  🚨 ALERT THRESHOLD REACHED — sending message…")
            direction = "▲" if change > 0 else "▼"
            msg = (
                f"{direction} **Price Alert (INR)**\n"
                f"Item: `{item}`\n"
                f"Old: ₹{old:.2f}\n"
                f"New: ₹{price:.2f}\n"
                f"Change: **{pct}%**"
            )
            send_discord_message(msg)

        prices[item] = {"price": price, "last_update": now}
        save_json(PRICE_CACHE, prices)

    print("\n========== ✅ All Done! ==========")


if __name__ == "__main__":
    main()
