import os
import json
import time
from datetime import datetime, timezone

import requests
import pandas as pd
import yfinance as yf


# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SEEN_FILE = "seen_golden_cross.json"

# Golden Cross definition
FAST_EMA = 9
SLOW_EMA = 21

# Daily candles
PERIOD = "3mo"
INTERVAL = "1d"

# Delay between Yahoo requests
REQUEST_DELAY = 0.15

# NSE equity list
NSE_EQUITY_URL = (
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
)

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
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            data=payload,
            timeout=30
        )

        if response.status_code == 200:
            print("Telegram alert sent.")
            return True

        print(
            "Telegram failed:",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:
        print("Telegram error:", e)

    return False


# ============================================================
# SEEN STATE
# ============================================================

def load_seen():
    if not os.path.exists(SEEN_FILE):
        return {}

    try:
        with open(
            SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data

    except Exception as e:
        print("Could not read seen file:", e)

    return {}


def save_seen(seen):
    with open(
        SEEN_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            seen,
            f,
            indent=2,
            sort_keys=True
        )


# ============================================================
# GET NSE SYMBOLS
# ============================================================

def get_nse_symbols():

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36"
        )
    }

    try:

        response = requests.get(
            NSE_EQUITY_URL,
            headers=headers,
            timeout=30
        )

        response.raise_for_status()

        from io import StringIO

        df = pd.read_csv(
            StringIO(response.text)
        )

        symbols = []

        for symbol in df["SYMBOL"].dropna():

            symbol = str(symbol).strip()

            if not symbol:
                continue

            # Yahoo Finance NSE format
            symbols.append(
                symbol + ".NS"
            )

        # Remove duplicates
        symbols = sorted(set(symbols))

        print(
            f"NSE symbols loaded: {len(symbols)}"
        )

        return symbols

    except Exception as e:

        print(
            "Could not download NSE symbol list:",
            e
        )

        return []


# ============================================================
# YAHOO DATA
# ============================================================

def get_stock_data(symbol):

    try:

        ticker = yf.Ticker(symbol)

        df = ticker.history(
            period=PERIOD,
            interval=INTERVAL,
            auto_adjust=False,
            actions=False
        )

        if df is None or df.empty:
            return None

        df = df.dropna(
            subset=["Close"]
        )

        if len(df) < SLOW_EMA + 5:
            return None

        return df

    except Exception as e:

        print(
            f"{symbol}: Yahoo error: {e}"
        )

        return None


# ============================================================
# GOLDEN CROSS DETECTION
# ============================================================

def find_fresh_golden_cross(symbol, df):

    try:

        close = df["Close"]

        df["EMA_FAST"] = close.ewm(
            span=FAST_EMA,
            adjust=False
        ).mean()

        df["EMA_SLOW"] = close.ewm(
            span=SLOW_EMA,
            adjust=False
        ).mean()

        # Need at least two completed candles
        if len(df) < 2:
            return None

        previous = df.iloc[-2]
        current = df.iloc[-1]

        previous_fast = float(
            previous["EMA_FAST"]
        )

        previous_slow = float(
            previous["EMA_SLOW"]
        )

        current_fast = float(
            current["EMA_FAST"]
        )

        current_slow = float(
            current["EMA_SLOW"]
        )

        # ====================================================
        # TRUE FRESH GOLDEN CROSS
        #
        # Previous:
        # 9 EMA <= 21 EMA
        #
        # Current:
        # 9 EMA > 21 EMA
        #
        # This prevents old golden crosses.
        # ====================================================

        fresh_cross = (
            previous_fast <= previous_slow
            and
            current_fast > current_slow
        )

        if not fresh_cross:
            return None

        cross_date = df.index[-1]

        if hasattr(cross_date, "date"):
            cross_date = cross_date.date()

        return {
            "symbol": symbol.replace(".NS", ""),
            "close": float(current["Close"]),
            "fast_ema": current_fast,
            "slow_ema": current_slow,
            "cross_date": str(cross_date),
        }

    except Exception as e:

        print(
            f"{symbol}: crossover error: {e}"
        )

        return None


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def format_message(data):

    symbol = data["symbol"]

    close = data["close"]
    fast_ema = data["fast_ema"]
    slow_ema = data["slow_ema"]

    cross_date = data["cross_date"]

    yahoo_url = (
        "https://finance.yahoo.com/quote/"
        f"{symbol}.NS/"
    )

    message = (
        "🟢 FRESH GOLDEN CROSS\n\n"
        f"🏢 Stock: {symbol}\n"
        f"📈 Timeframe: 1 DAY\n"
        f"📅 Cross Date: {cross_date}\n\n"
        f"💰 Close: ₹{close:.2f}\n"
        f"9 EMA: ₹{fast_ema:.2f}\n"
        f"21 EMA: ₹{slow_ema:.2f}\n\n"
        "9 EMA crossed ABOVE 21 EMA\n\n"
        "🔗 Stock:\n"
        f"{yahoo_url}\n\n"
        "🤖 NSE Fresh Golden Cross Scanner"
    )

    return message


# ============================================================
# MAIN SCANNER
# ============================================================

def main():

    print("=" * 60)
    print("NSE FRESH GOLDEN CROSS SCANNER")
    print("=" * 60)

    seen = load_seen()

    print(
        f"Previously alerted: {len(seen)}"
    )

    symbols = get_nse_symbols()

    if not symbols:
        print("No symbols received.")
        return

    fresh_count = 0
    duplicate_count = 0
    checked_count = 0

    for symbol in symbols:

        checked_count += 1

        print(
            f"[{checked_count}/{len(symbols)}] "
            f"Checking {symbol}"
        )

        df = get_stock_data(symbol)

        if df is None:
            continue

        result = find_fresh_golden_cross(
            symbol,
            df
        )

        if result is None:
            continue

        stock = result["symbol"]
        cross_date = result["cross_date"]

        # ====================================================
        # UNIQUE ALERT ID
        #
        # Same stock + same cross date
        # will NEVER be sent twice.
        # ====================================================

        alert_id = (
            f"{stock}|"
            f"{FAST_EMA}|"
            f"{SLOW_EMA}|"
            f"{cross_date}"
        )

        if alert_id in seen:

            print(
                f"{stock}: already alerted "
                f"for {cross_date}"
            )

            duplicate_count += 1
            continue

        print(
            f"🟢 FRESH GOLDEN CROSS: "
            f"{stock} - {cross_date}"
        )

        message = format_message(
            result
        )

        sent = send_telegram(
            message
        )

        if sent:

            seen[alert_id] = {
                "symbol": stock,
                "cross_date": cross_date,
                "sent_at": datetime.now(
                    timezone.utc
                ).isoformat()
            }

            save_seen(seen)

            fresh_count += 1

        time.sleep(
            REQUEST_DELAY
        )

    # Final save
    save_seen(seen)

    print()
    print("=" * 60)
    print("SCAN FINISHED")
    print("=" * 60)

    print(
        f"Stocks checked: {checked_count}"
    )

    print(
        f"Fresh golden crosses: {fresh_count}"
    )

    print(
        f"Duplicate crosses skipped: "
        f"{duplicate_count}"
    )

    print(
        f"Total saved alerts: {len(seen)}"
    )

    print("=" * 60)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
