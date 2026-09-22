import os
import json
import time
from datetime import datetime, timezone

import yfinance as yf
import pandas as pd
import requests


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ============================================================
# SETTINGS
# ============================================================

SHORT_EMA = 50
LONG_EMA = 200

TIMEFRAME = "1d"

STATE_FILE = "seen.json"

# Delay between Yahoo requests
REQUEST_DELAY = 0.25


# ============================================================
# LOAD / SAVE STATE
# ============================================================

def load_seen():
    if not os.path.exists(STATE_FILE):
        return set()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return set(data)

        return set()

    except Exception:
        return set()


def save_seen(seen):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, indent=2)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials are missing.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=30
        )

        print("Telegram:", response.status_code)

        if response.ok:
            return True

        print(response.text)
        return False

    except Exception as e:
        print("Telegram error:", e)
        return False


# ============================================================
# NIFTY 500 SYMBOLS
# ============================================================

def get_nifty500_symbols():

    url = (
        "https://archives.nseindia.com/"
        "content/indices/ind_nifty500list.csv"
    )

    headers = {
        "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36",
        "Accept": "text/csv,*/*"
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            timeout=30
        )

        response.raise_for_status()

        from io import StringIO

        df = pd.read_csv(StringIO(response.text))

        if "Symbol" not in df.columns:
            raise Exception("Symbol column not found")

        symbols = []

        for symbol in df["Symbol"].dropna():

            symbol = str(symbol).strip().upper()

            if symbol:
                symbols.append(symbol + ".NS")

        symbols = sorted(set(symbols))

        print(f"Nifty 500 symbols loaded: {len(symbols)}")

        return symbols

    except Exception as e:

        print("Could not download Nifty 500 list:")
        print(e)

        return []


# ============================================================
# CHECK ONE STOCK
# ============================================================

def check_golden_cross(symbol):

    try:

        ticker = yf.Ticker(symbol)

        df = ticker.history(
            period="1y",
            interval="1d",
            auto_adjust=False
        )

        if df.empty:
            return None

        if len(df) < LONG_EMA + 5:
            print(
                f"{symbol}: insufficient data"
            )
            return None

        # Remove rows without Close
        df = df.dropna(subset=["Close"])

        # Calculate EMA
        df["EMA50"] = (
            df["Close"]
            .ewm(
                span=SHORT_EMA,
                adjust=False
            )
            .mean()
        )

        df["EMA200"] = (
            df["Close"]
            .ewm(
                span=LONG_EMA,
                adjust=False
            )
            .mean()
        )

        if len(df) < 2:
            return None

        # ----------------------------------------------------
        # TODAY
        # ----------------------------------------------------

        today = df.iloc[-1]

        # ----------------------------------------------------
        # PREVIOUS TRADING DAY
        # ----------------------------------------------------

        previous = df.iloc[-2]

        today_ema50 = float(today["EMA50"])
        today_ema200 = float(today["EMA200"])

        previous_ema50 = float(previous["EMA50"])
        previous_ema200 = float(previous["EMA200"])

        today_close = float(today["Close"])

        today_date = df.index[-1].date()

        # ----------------------------------------------------
        # TRUE FRESH GOLDEN CROSS
        #
        # Previous:
        # 50 EMA <= 200 EMA
        #
        # Today:
        # 50 EMA > 200 EMA
        # ----------------------------------------------------

        fresh_cross = (
            previous_ema50 <= previous_ema200
            and
            today_ema50 > today_ema200
        )

        if not fresh_cross:
            return None

        clean_symbol = symbol.replace(".NS", "")

        # Yahoo URL
        stock_url = (
            f"https://finance.yahoo.com/quote/"
            f"{clean_symbol}.NS/"
        )

        return {
            "symbol": clean_symbol,
            "date": str(today_date),
            "close": today_close,
            "ema50": today_ema50,
            "ema200": today_ema200,
            "previous_ema50": previous_ema50,
            "previous_ema200": previous_ema200,
            "url": stock_url
        }

    except Exception as e:

        print(
            f"{symbol}: error -> {e}"
        )

        return None


# ============================================================
# FORMAT TELEGRAM MESSAGE
# ============================================================

def format_message(data):

    return (
        "🟢 FRESH GOLDEN CROSS\n\n"

        f"🏢 Stock: {data['symbol']}\n"
        f"📈 Timeframe: 1 DAY\n"
        f"📅 Cross Date: {data['date']}\n\n"

        f"💰 Close: ₹{data['close']:.2f}\n"
        f"50 EMA: ₹{data['ema50']:.2f}\n"
        f"200 EMA: ₹{data['ema200']:.2f}\n\n"

        "📊 Previous Trading Day:\n"
        f"50 EMA: ₹{data['previous_ema50']:.2f}\n"
        f"200 EMA: ₹{data['previous_ema200']:.2f}\n\n"

        "✅ 50 EMA crossed ABOVE 200 EMA\n\n"

        f"🔗 Stock:\n{data['url']}\n\n"

        "🤖 NSE Fresh Golden Cross Scanner"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("NSE FRESH GOLDEN CROSS SCANNER")
    print("=" * 60)

    print()
    print("Definition:")
    print("Previous day: 50 EMA <= 200 EMA")
    print("Today:        50 EMA > 200 EMA")
    print()

    seen = load_seen()

    print(
        f"Previously alerted: {len(seen)}"
    )

    symbols = get_nifty500_symbols()

    if not symbols:

        print("No symbols loaded.")
        print("Scanner stopped.")

        return

    alerts = 0

    for count, symbol in enumerate(symbols, start=1):

        print(
            f"[{count}/{len(symbols)}] "
            f"Checking {symbol}"
        )

        result = check_golden_cross(symbol)

        if result:

            alert_key = (
                f"{result['symbol']}_"
                f"{result['date']}_"
                f"50_200"
            )

            # -----------------------------------------------
            # DO NOT SEND OLD / REPEATED ALERT
            # -----------------------------------------------

            if alert_key in seen:

                print(
                    f"{symbol}: already alerted"
                )

                continue

            message = format_message(result)

            print()
            print(
                f"🔥 FRESH GOLDEN CROSS FOUND: "
                f"{result['symbol']}"
            )
            print()

            sent = send_telegram(message)

            if sent:

                seen.add(alert_key)

                alerts += 1

                print(
                    f"{symbol}: Telegram alert sent"
                )

            else:

                print(
                    f"{symbol}: Telegram failed"
                )

        time.sleep(REQUEST_DELAY)

    save_seen(seen)

    print()
    print("=" * 60)
    print("SCAN FINISHED")
    print(f"Fresh alerts sent: {alerts}")
    print(f"Total saved alerts: {len(seen)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
