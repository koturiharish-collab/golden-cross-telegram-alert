import os
import time
import json
import requests
import pandas as pd
import yfinance as yf
from io import StringIO
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = "golden_cross_state.json"
NSE_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        },
        timeout=20
    )

    response.raise_for_status()


def get_nse_symbols():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/140 Safari/537.36"
        )
    }

    response = requests.get(
        NSE_URL,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()

    df = pd.read_csv(StringIO(response.text))

    # Only normal NSE equity shares
    df = df[df[" SERIES"].astype(str).str.strip() == "EQ"]

    symbols = (
        df["SYMBOL"]
        .astype(str)
        .str.strip()
        .dropna()
        .unique()
        .tolist()
    )

    return symbols


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def check_stock(symbol, state):
    ticker = f"{symbol}.NS"

    try:
        data = yf.download(
            ticker,
            period="1y",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False
        )

        if data.empty:
            return

        # Handle yfinance multi-index columns
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        data = data.dropna(subset=["Close"])

        # Need enough history for 200-day SMA
        if len(data) < 205:
            return

        data["SMA50"] = data["Close"].rolling(50).mean()
        data["SMA200"] = data["Close"].rolling(200).mean()

        # Use completed daily candles only.
        # Last two rows are the most recent daily candles available.
        previous = data.iloc[-2]
        current = data.iloc[-1]

        if pd.isna(previous["SMA50"]) or pd.isna(previous["SMA200"]):
            return

        if pd.isna(current["SMA50"]) or pd.isna(current["SMA200"]):
            return

        # Fresh Golden Cross:
        # Previous day: 50 SMA <= 200 SMA
        # Current day:  50 SMA > 200 SMA
        fresh_cross = (
            previous["SMA50"] <= previous["SMA200"]
            and current["SMA50"] > current["SMA200"]
        )

        if not fresh_cross:
            return

        cross_date = current.name.strftime("%Y-%m-%d")

        # Prevent repeated alerts for the same crossover
        if state.get(symbol) == cross_date:
            return

        close_price = float(current["Close"])
        sma50 = float(current["SMA50"])
        sma200 = float(current["SMA200"])

        message = (
            "🟢 FRESH GOLDEN CROSS\n\n"
            f"Stock: {symbol}\n"
            f"Timeframe: 1 DAY\n"
            f"Cross Date: {cross_date}\n"
            f"Close: ₹{close_price:.2f}\n"
            f"50 SMA: ₹{sma50:.2f}\n"
            f"200 SMA: ₹{sma200:.2f}\n\n"
            "50 SMA crossed ABOVE 200 SMA."
        )

        send_telegram(message)

        state[symbol] = cross_date

        print(f"ALERT SENT: {symbol} - {cross_date}")

    except Exception as e:
        print(f"ERROR {symbol}: {e}")


def main():
    print("Starting NSE Golden Cross scanner...")

    symbols = get_nse_symbols()

    print(f"Found {len(symbols)} NSE EQ stocks.")

    state = load_state()

    for i, symbol in enumerate(symbols, start=1):
        print(f"[{i}/{len(symbols)}] Checking {symbol}")

        check_stock(symbol, state)

        # Small delay to reduce data-provider pressure
        time.sleep(0.3)

    save_state(state)

    print("Scan completed.")


if __name__ == "__main__":
    main()
