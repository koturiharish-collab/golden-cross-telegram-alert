"""Golden Cross (SMA50 crosses above SMA200) scanner on the DAILY timeframe.
Sends fresh alerts to Telegram. Dedupes via state.json so each cross alerts once.
Usage: python golden_cross_alert.py [--test]
"""
import json
import os
import sys
from pathlib import Path

import requests
import yfinance as yf

FAST = int(os.getenv("FAST_MA", 50))
SLOW = int(os.getenv("SLOW_MA", 200))
FRESH_BARS = int(os.getenv("FRESH_BARS", 1))  # cross must be within last N daily bars
INCLUDE_LIVE_BAR = os.getenv("INCLUDE_LIVE_BAR", "true").lower() == "true"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STATE_FILE = Path("state.json")
SYMBOLS_FILE = Path("symbols.txt")


def load_symbols():
    lines = SYMBOLS_FILE.read_text().splitlines()
    return [l.split("#")[0].strip() for l in lines if l.split("#")[0].strip()]


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def send_telegram(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=20,
    )
    r.raise_for_status()


def find_fresh_cross(symbol):
    """Return dict for the most recent golden cross within FRESH_BARS, else None."""
    df = yf.Ticker(symbol).history(period="2y", interval="1d", auto_adjust=True)
    close = df["Close"].dropna()
    if not INCLUDE_LIVE_BAR and len(close) > 1:
        # Drop today's still-forming candle if the market is open / bar is today
        if close.index[-1].date() == __import__("datetime").date.today():
            close = close.iloc[:-1]
    if len(close) < SLOW + 2:
        print(f"[skip] {symbol}: only {len(close)} bars")
        return None

    diff = close.rolling(FAST).mean() - close.rolling(SLOW).mean()
    crossed = (diff.shift(1) <= 0) & (diff > 0)
    recent = crossed.iloc[-FRESH_BARS:]
    if not recent.any():
        return None

    when = recent[recent].index[-1]
    return {
        "date": when.strftime("%Y-%m-%d"),
        "close": float(close.loc[when]),
        "fast": float(close.rolling(FAST).mean().loc[when]),
        "slow": float(close.rolling(SLOW).mean().loc[when]),
    }


def main():
    if not TOKEN or not CHAT_ID:
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")

    if "--test" in sys.argv:
        send_telegram("✅ Golden Cross bot is connected.")
        return

    state = load_state()
    changed = False

    for sym in load_symbols():
        try:
            hit = find_fresh_cross(sym)
        except Exception as e:  # keep scanning other symbols
            print(f"[error] {sym}: {e}")
            continue
        if not hit:
            print(f"[none] {sym}")
            continue
        if state.get(sym, "") >= hit["date"]:
            print(f"[dup]  {sym} already alerted for {hit['date']}")
            continue

        msg = (
            f"🟡 <b>GOLDEN CROSS</b> — <code>{sym}</code>\n"
            f"Timeframe: Daily\n"
            f"SMA{FAST} crossed above SMA{SLOW} on {hit['date']}\n"
            f"Price: {hit['close']:.2f}\n"
            f"SMA{FAST}: {hit['fast']:.2f} | SMA{SLOW}: {hit['slow']:.2f}"
        )
        try:
            send_telegram(msg)
        except Exception as e:
            print(f"[telegram error] {sym}: {e}")  # not saved -> retried next run
            continue
        state[sym] = hit["date"]
        changed = True
        print(f"[ALERT] {sym} {hit['date']}")

    if changed:
        STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
