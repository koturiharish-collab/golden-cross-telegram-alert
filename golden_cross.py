import os
import json
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import yfinance as yf


# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

STATE_FILE = "alert_state.json"

EMA_FAST = 50
EMA_SLOW = 200

NIFTY_500_URL = (
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERROR: Telegram secrets are missing.")
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
            data=payload,
            timeout=20
        )

        print("Telegram status:", response.status_code)
        print("Telegram response:", response.text[:500])

        return response.ok

    except Exception as e:

        print("Telegram error:", e)
        return False


# ============================================================
# LOAD STATE
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return {}


# ============================================================
# SAVE STATE
# ============================================================

def save_state(state):

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            indent=2,
            sort_keys=True
        )


# ============================================================
# GET NSE SYMBOLS
# ============================================================

def get_nse_symbols():

    print("Downloading NSE NIFTY 500 list...")

    try:

        df = pd.read_csv(NIFTY_500_URL)

        if "Symbol" not in df.columns:

            print("NSE Symbol column not found.")
            return []

        symbols = (
            df["Symbol"]
            .dropna()
            .astype(str)
            .str.strip()
            .tolist()
        )

        symbols = sorted(set(symbols))

        print(
            "Total NSE symbols:",
            len(symbols)
        )

        return symbols

    except Exception as e:

        print(
            "Unable to download NSE list:",
            e
        )

        return []


# ============================================================
# GET DAILY STOCK DATA
# ============================================================

def get_stock_data(symbol):

    ticker = f"{symbol}.NS"

    try:

        df = yf.download(
            ticker,
            period="2y",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False
        )

        if df is None or df.empty:
            return None

        # Yahoo sometimes returns MultiIndex columns
        if isinstance(df.columns, pd.MultiIndex):

            df.columns = (
                df.columns
                .get_level_values(0)
            )

        if "Close" not in df.columns:
            return None

        df = df[["Close"]].copy()

        df["Close"] = pd.to_numeric(
            df["Close"],
            errors="coerce"
        )

        df.dropna(inplace=True)

        if len(df) < 210:
            return None

        return df

    except Exception as e:

        print(
            f"{symbol}: data error -> {e}"
        )

        return None


# ============================================================
# CHECK FRESH GOLDEN CROSS
# ============================================================

def check_golden_cross(symbol):

    df = get_stock_data(symbol)

    if df is None:
        return None

    # --------------------------------------------------------
    # REMOVE CURRENT / POSSIBLY UNFINISHED DAILY CANDLE
    # --------------------------------------------------------

    completed = df.iloc[:-1].copy()

    if len(completed) < 205:
        return None

    # --------------------------------------------------------
    # CALCULATE 50 EMA AND 200 EMA
    # --------------------------------------------------------

    completed["EMA50"] = (
        completed["Close"]
        .ewm(
            span=EMA_FAST,
            adjust=False
        )
        .mean()
    )

    completed["EMA200"] = (
        completed["Close"]
        .ewm(
            span=EMA_SLOW,
            adjust=False
        )
        .mean()
    )

    # --------------------------------------------------------
    # LAST TWO COMPLETED DAILY CANDLES
    # --------------------------------------------------------

    previous = completed.iloc[-2]
    current = completed.iloc[-1]

    previous_50 = float(
        previous["EMA50"]
    )

    previous_200 = float(
        previous["EMA200"]
    )

    current_50 = float(
        current["EMA50"]
    )

    current_200 = float(
        current["EMA200"]
    )

    # --------------------------------------------------------
    # TRUE FRESH GOLDEN CROSS
    #
    # PREVIOUS:
    # 50 EMA <= 200 EMA
    #
    # CURRENT:
    # 50 EMA > 200 EMA
    # --------------------------------------------------------

    fresh_cross = (
        previous_50 <= previous_200
        and
        current_50 > current_200
    )

    if not fresh_cross:

        return None

    # --------------------------------------------------------
    # CROSS DATE
    # --------------------------------------------------------

    cross_date = current.name

    if hasattr(
        cross_date,
        "strftime"
    ):

        cross_date = cross_date.strftime(
            "%Y-%m-%d"
        )

    else:

        cross_date = str(cross_date)

    close_price = float(
        current["Close"]
    )

    return {

        "symbol": symbol,

        "cross_date": cross_date,

        "close": close_price,

        "ema50": current_50,

        "ema200": current_200

    }


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 65)
    print("NSE FRESH GOLDEN CROSS SCANNER")
    print("=" * 65)

    print(
        "Run time:",
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    state = load_state()

    symbols = get_nse_symbols()

    if not symbols:

        print("No symbols received.")
        return

    new_alerts = 0

    # --------------------------------------------------------
    # SCAN ALL STOCKS
    # --------------------------------------------------------

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        print(
            f"[{index}/{len(symbols)}] "
            f"Checking {symbol}"
        )

        result = check_golden_cross(
            symbol
        )

        if result is None:
            continue

        # ----------------------------------------------------
        # UNIQUE ALERT
        #
        # SAME STOCK + SAME DATE
        # = SAME ALERT
        # ----------------------------------------------------

        alert_key = (
            f"{result['symbol']}_"
            f"{result['cross_date']}"
        )

        if alert_key in state:

            print(
                f"{symbol}: already alerted "
                f"for {result['cross_date']}"
            )

            continue

        # ----------------------------------------------------
        # TELEGRAM MESSAGE
        # ----------------------------------------------------

        message = (
            "🟢 FRESH GOLDEN CROSS\n\n"

            f"📊 Stock: {result['symbol']}\n"
            f"📅 Cross Date: {result['cross_date']}\n\n"

            f"💰 Close: ₹{result['close']:.2f}\n"
            f"📈 50 EMA: ₹{result['ema50']:.2f}\n"
            f"📉 200 EMA: ₹{result['ema200']:.2f}\n\n"

            "⚠️ 50 EMA crossed ABOVE 200 EMA\n\n"

            f"🔗 Stock:\n"
            f"https://finance.yahoo.com/quote/"
            f"{result['symbol']}.NS/\n\n"

            "🤖 NSE Fresh Golden Cross Scanner"
        )

        print()
        print(message)
        print()

        sent = send_telegram(
            message
        )

        if sent:

            state[alert_key] = {

                "symbol":
                    result["symbol"],

                "cross_date":
                    result["cross_date"],

                "ema50":
                    result["ema50"],

                "ema200":
                    result["ema200"],

                "sent_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat()
            }

            save_state(state)

            new_alerts += 1

            print(
                f"✅ Telegram alert sent: "
                f"{symbol}"
            )

        else:

            print(
                f"❌ Telegram alert failed: "
                f"{symbol}"
            )

        time.sleep(0.3)

    print()
    print("=" * 65)
    print("SCAN FINISHED")
    print(
        "New alerts:",
        new_alerts
    )
    print(
        "Total saved alerts:",
        len(state)
    )
    print("=" * 65)


if __name__ == "__main__":
    main()
