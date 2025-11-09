# Steam Price Monitor Bot

Tracks the price of your Steam CS:GO/CS2 inventory items and sends Discord alerts when the price changes by more than 10%.  
Designed to run automatically using GitHub Actions.

## ✅ Features
- Tracks **entire Steam inventory** or `items.txt`
- Uses official Steam Market price API
- Sends **Discord alerts** for big price changes
- Saves price logs to `history.csv`
- Caches prices to avoid rate limits
- Safe throttling + retry handling
- Designed for **GitHub Actions** (runs every 30 min)

## ✅ Requirements
Only one Python dependency:
requests


## ✅ Setup Instructions

### 1. Add Secrets
Go to:
**GitHub → Repository → Settings → Secrets → Actions**

Add:

- `STEAM_ID64` → Your Steam 64-bit ID  
- `DISCORD_WEBHOOK` → Your Discord webhook URL  

### 2. Files you need
bot.py
items.txt (optional)
requirements.txt
.github/workflows/check.yml


### 3. Trigger the bot
GitHub → Actions → Steam Price Monitor → **Run workflow**

## ✅ Steam Currency
Change currency in `bot.py`:
CURRENCY = 24 # INR

(INR = 24, USD = 1, EUR = 3, etc.)

## ✅ Notes
- This bot **does not trade** or modify your account.
- It only performs simple HTTP GET requests.
- 100% safe and not bannable.
