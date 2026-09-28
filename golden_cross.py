"""Golden Cross (SMA50 crosses above SMA200) scanner - DAILY timeframe - ALL NSE stocks.
Sends only fresh alerts to Telegram; dedupes via state.json.
Usage: python golden_cross_alert.py [--test]
"""
import datetime as dt
import html
import io
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
import yfinance as yf

FAST = int(os.getenv("FAST_MA", 50))
SLOW = int(os.getenv("SLOW_MA", 200))
FRESH_BARS = int(os.getenv("FRESH_BARS", 1))          # cross must be within last N daily bars
INCLUDE_LIVE_BAR = os.getenv("INCLUDE_LIVE_BAR", "true").lower() == "true"
NSE_ALL = os.getenv("NSE_ALL", "true").lower() == "true"
NSE_SERIES = {s.strip() for s in os.getenv("NSE_SERIES", "EQ").split(",")}
MIN_AVG_VOLUME = int(os.getenv("MIN_AVG_VOLUME", 0))  # 20-day avg volume filter (0 = off)
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 150))
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STATE_FILE = Path("state.json")
CACHE_FILE = Path("nse_equities.txt")   # cached NSE symbol list (fallback if NSE blocks download)
EXTRA_FILE = Path("symbols.txt")        # optional extra Yahoo tickers
NSE_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"


# ---------- universe ----------
def get_nse_symbols():
    try:
        r = requests.get(
            NSE_URL,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                     "Accept": "text/csv,*/*"},
            timeout=30,
        )
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = [c.strip() for c in df.columns]
        df["SERIES"] = df["SERIES"].astype(str).str.strip()
        syms = sorted(df.loc[df["SERIES"].isin(NSE_SERIES), "SYMBOL"].astype(str).str.strip().unique())
        if len(syms) > 500:
            text = "\n".join(syms) + "\n"
            if not CACHE_FILE.exists() or CACHE_FILE.read_text() != text:
                CACHE_FILE.write_text(text)
            return syms
        print(f"[warn] NSE list looked too small ({len(syms)}), using cache")
    except Exception as e:
        print(f"[warn] NSE list download failed: {e}")
    if CACHE_FILE.exists():
        return CACHE_FILE.read_text().split()
    sys.exit("Could not get NSE stock list. Put a list of NSE symbols (one per line) in nse_equities.txt")


def load_universe():
    tickers = []
    if NSE_ALL:
        tickers += [f"{s}.NS" for s in get_nse_symbols()]
    if EXTRA_FILE.exists():
        for line in EXTRA_FILE.read_text().splitlines():
            t = line.split("#")[0].strip()
            if t:
                tickers.append(t)
    return list(dict.fromkeys(tickers))


# ---------- signal ----------
def detect_cross(close, volume=None):
    """Return dict if SMA(FAST) crossed above SMA(SLOW) within last FRESH_BARS bars."""
    close = close.dropna()
    if not INCLUDE_LIVE_BAR and len(close) > 1 and close.index[-1].date() == dt.date.today():
        close = close.iloc[:-1]
    if len(close) < SLOW + 2:
        return None
    fast, slow = close.rolling(FAST).mean(), close.rolling(SLOW).mean()
    diff = fast - slow
    crossed = (diff.shift(1) <= 0) & (diff > 0)
    recent = crossed.iloc[-FRESH_BARS:]
    if not recent.any():
        return None
    if MIN_AVG_VOLUME and volume is not None:
        if volume.reindex(close.index).tail(20).mean() < MIN_AVG_VOLUME:
            return None
    when = recent[recent].index[-1]
    return {"date": when.strftime("%Y-%m-%d"), "close": float(close.loc[when]),
            "fast": float(fast.loc[when]), "slow": float(slow.loc[when])}


def download(tickers):
    for attempt in range(3):
        try:
            return yf.download(tickers, period="1y", interval="1d", group_by="ticker",
                               auto_adjust=True, threads=True, progress=False)
        except Exception as e:
            print(f"[retry {attempt + 1}] download failed: {e}")
            time.sleep(5 * (attempt + 1))
    return None


def scan(tickers):
    hits = {}
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i:i + BATCH_SIZE]
        data = download(batch)
        print(f"[batch {i // BATCH_SIZE + 1}] {len(batch)} tickers")
        if data is None or data.empty:
            continue
        multi = isinstance(data.columns, pd.MultiIndex)
        present = set(data.columns.get_level_values(0)) if multi else set(batch)
        for t in batch:
            if t not in present:
                continue
            df = data[t] if multi else data
            if "Close" not in df:
                continue
            try:
                hit = detect_cross(df["Close"], df.get("Volume"))
            except Exception as e:
                print(f"[error] {t}: {e}")
                continue
            if hit:
                hits[t] = hit
        time.sleep(2)
    return hits


# ---------- telegram / state ----------
def send_telegram(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=20,
    )
    r.raise_for_status()


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def line_for(t, h):
    name = t.replace(".NS", "")
    link = f"https://www.tradingview.com/chart/?symbol=NSE:{quote(name)}" if t.endswith(".NS") else None
    label = f'<a href="{link}">{html.escape(name)}</a>' if link else html.escape(t)
    return f"• <b>{label}</b> ₹{h['close']:.2f} | SMA{FAST} {h['fast']:.1f} > SMA{SLOW} {h['slow']:.1f} | {h['date']}"


def main():
    if not TOKEN or not CHAT_ID:
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    if "--test" in sys.argv:
        send_telegram("✅ Golden Cross bot is connected.")
        return

    universe = load_universe()
    print(f"Scanning {len(universe)} tickers")
    state = load_state()
    hits = scan(universe)

    fresh = {t: h for t, h in hits.items() if state.get(t, "") < h["date"]}
    print(f"{len(hits)} crosses found, {len(fresh)} new")

    items = sorted(fresh.items())
    for i in range(0, len(items), 15):
        chunk = items[i:i + 15]
        msg = (f"🟡 <b>GOLDEN CROSS (Daily)</b> — {len(items)} new\n"
               + "\n".join(line_for(t, h) for t, h in chunk))
        try:
            send_telegram(msg)
        except Exception as e:
            print(f"[telegram error] {e}")  # not saved -> retried next run
            continue
        for t, h in chunk:
            state[t] = h["date"]
        time.sleep(1.5)

    new_text = json.dumps(state, indent=2, sort_keys=True)
    if not STATE_FILE.exists() or STATE_FILE.read_text() != new_text:
        STATE_FILE.write_text(new_text)


if __name__ == "__main__":
    main()
