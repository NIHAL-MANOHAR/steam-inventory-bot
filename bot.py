#!/usr/bin/env python3
"""
steam-price-bot - tracks Steam market prices for items (items.txt OR inventory)
Sends Discord alerts (DISCORD_WEBHOOK) on >=10% price moves.
Also sends 3-hour average alerts to DISCORD_WEBHOOK_3HR (>=5% change).
Designed for GitHub Actions usage.
"""

import os
import time
import json
import requests
import urllib.parse
from datetime import datetime, timezone
from tqdm import tqdm

# === CONFIG ===
CURRENCY = 24
PRICE_CACHE = ".data/prices.json"
HISTORY_CSV = ".data/history.csv"
ITEMS_FILE = "items.txt"
STEAM_ID_ENV = "STEAM_ID64"
DISCORD_WEBHOOK_ENV = "DISCORD_WEBHOOK"
DISCORD_WEBHOOK_3HR_ENV = "DISCORD_WEBHOOK_3HR"  # new webhook
DELAY_SEC = 1.2
RETRY_BASE = 2
MIN_ALERT_PCT = 0.10       # 10% for price change alert
MIN_3HR_ALERT_PCT = 0.05   # 5% for 3-hour average alert
# ==============

STEAM_ID = os.getenv(STEAM_ID_ENV)
DISCORD_WEBHOOK = os.getenv(DISCORD_WEBHOOK_ENV)
DISCORD_WEBHOOK_3HR = os.getenv(DISCORD_WEBHOOK_3HR_ENV)

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

def send_3hr_alert(text):
    if not DISCORD_WEBHOOK_3HR:
        print("  3-hour webhook not set, skipping message.")
        return
    payload = {"content": text}
    try:
        requests.post(DISCORD_WEBHOOK_3HR, json=payload, timeout=10)
        print("  3-hour average alert sent ✅")
    except Exception as e:
        print("  Failed to post 3-hour average alert:", e)

def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def append_history(item, price):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f'{ts},{item},{price}\n'  # no quotes to avoid parsing issues

    if not os.path.exists(HISTORY_CSV):
        with open(HISTORY_CSV, "w", encoding="utf-8") as f:
            f.write("timestamp,item,price\n")

    with open(HISTORY_CSV, "a", encoding="utf-8") as f:
        f.write(line)

def load_items_list():
    if os.path.exists(ITEMS_FILE):
        with open(ITEMS_FILE, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
            if lines:
                print(f"Loaded {len(lines)} items from {ITEMS_FILE}")
                return list(dict.fromkeys(lines))

    if not STEAM_ID:
        print("No items.txt and STEAM_ID64 not set. Nothing to do.")
        return []

    url = f"https://steamcommunity.com/inventory/{STEAM_ID}/730/2?l=english&count=2000"
    data = safe_get_json(url)
    if not data:
        print("Failed to fetch inventory.")
        return []

    descriptions = data.get("descriptions", [])
    marketable = [d["market_hash_name"] for d in descriptions if d.get("marketable", 0) == 1]
    unique = list(dict.fromkeys(marketable))
    print(f"Loaded {len(unique)} marketable items from Steam inventory.")
    return unique

def fetch_price_for_item(item_name):
    q = urllib.parse.quote(item_name, safe='')
    url = f"https://steamcommunity.com/market/priceoverview/?appid=730&currency={CURRENCY}&market_hash_name={q}"
    data = safe_get_json(url)
    if not data:
        return None
    price_str = data.get("lowest_price") or data.get("median_price")
    if not price_str:
        return None
    cleaned = price_str.replace("₹", "").replace("INR", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except:
        return None

def get_3hr_avg(item):
    if not os.path.exists(HISTORY_CSV):
        return None
    now = datetime.now(timezone.utc)
    three_hours_ago = now.timestamp() - 3*3600
    prices_last_3hr = []
    with open(HISTORY_CSV, "r", encoding="utf-8") as f:
        next(f)  # skip header
        for line in f:
            ts_str, line_item, price_str = line.strip().split(",")
            line_item = line_item.strip()
            if line_item != item:
                continue
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if ts.timestamp() >= three_hours_ago:
                    prices_last_3hr.append(float(price_str))
            except:
                continue
    if prices_last_3hr:
        return sum(prices_last_3hr)/len(prices_last_3hr)
    return None

def main():
    print("========== Steam Price Bot Starting ==========")
    prices = load_json(PRICE_CACHE)
    items = load_items_list()
    if not items:
        print("No items to check. Exiting.")
        return

    for idx, item in enumerate(tqdm(items, desc="Checking Prices", unit="item"), start=1):
        print(f"\n--- [{idx}/{len(items)}] Processing item: {item} ---")
        entry = prices.get(item, {})
        now = time.time()
        last_update = entry.get("last_update", 0)
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

        old = entry.get("price", price)
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

        # --- 3-hour average check ---
        avg_3hr = get_3hr_avg(item)
        if avg_3hr is None:
            avg_3hr = price  # fallback
        print(f"  3-hour avg price: ₹{avg_3hr:.2f}")

        # Trigger only if >=5% change from 3-hour average
        change_3hr = (price - avg_3hr) / avg_3hr
        if abs(change_3hr) >= MIN_3HR_ALERT_PCT:
            direction = "▲" if change_3hr > 0 else "▼"
            pct_3hr = round(change_3hr * 100, 2)
            msg_3hr = (
                f"{direction} **3-Hour Avg Alert (INR)**\n"
                f"Item: `{item}`\n"
                f"Current price: ₹{price:.2f}\n"
                f"3-hour avg: ₹{avg_3hr:.2f}\n"
                f"Change: **{pct_3hr}%**"
            )
            send_3hr_alert(msg_3hr)

        # --- Save current price and 3-hour avg in JSON ---
        prices[item] = {"price": price, "last_update": now, "avg_3hr": avg_3hr}
        save_json(PRICE_CACHE, prices)

    print("\n========== ✅ All Done! ==========")

if __name__ == "__main__":
    main()
