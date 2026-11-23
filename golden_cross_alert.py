"""Golden Cross (50 EMA above 200 EMA) + Death Cross (50 EMA below 200 EMA) scanner - DAILY - ALL NSE stocks.
Set MA_TYPE=SMA for simple averages (default is EMA, matching TradingView 50/200 EMA).
Sends only fresh alerts to Telegram; dedupes via state.json.
Usage: python golden_cross_alert.py [--test]
"""
import datetime as dt
import base64
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
MA_TYPE = os.getenv("MA_TYPE", "EMA").upper()          # EMA (default) or SMA
FRESH_BARS = int(os.getenv("FRESH_BARS", 1))          # cross must be within last N daily bars
INCLUDE_LIVE_BAR = os.getenv("INCLUDE_LIVE_BAR", "true").lower() == "true"
ENABLE_GOLDEN = os.getenv("ENABLE_GOLDEN", "true").lower() == "true"
ENABLE_DEATH = os.getenv("ENABLE_DEATH", "true").lower() == "true"
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
def ma(series, n):
    if MA_TYPE == "EMA":
        return series.ewm(span=n, adjust=False).mean()   # same formula TradingView uses
    return series.rolling(n).mean()


def detect_cross(close, volume=None):
    """Return {'golden': hit|None, 'death': hit|None} for crosses within last FRESH_BARS bars."""
    out = {"golden": None, "death": None}
    close = close.dropna()
    if not INCLUDE_LIVE_BAR and len(close) > 1 and close.index[-1].date() == dt.date.today():
        close = close.iloc[:-1]
    if len(close) < SLOW + 2:
        return out
    fast, slow = ma(close, FAST), ma(close, SLOW)
    diff = fast - slow
    up = (diff.shift(1) <= 0) & (diff > 0)
    down = (diff.shift(1) >= 0) & (diff < 0)
    if MIN_AVG_VOLUME and volume is not None:
        if volume.reindex(close.index).tail(20).mean() < MIN_AVG_VOLUME:
            return out
    for kind, series, enabled in (("golden", up, ENABLE_GOLDEN), ("death", down, ENABLE_DEATH)):
        recent = series.iloc[-FRESH_BARS:]
        if enabled and recent.any():
            when = recent[recent].index[-1]
            out[kind] = {"date": when.strftime("%Y-%m-%d"), "close": float(close.loc[when]),
                         "fast": float(fast.loc[when]), "slow": float(slow.loc[when])}
    return out


def download(tickers):
    for attempt in range(3):
        try:
            return yf.download(tickers, period="3y", interval="1d", group_by="ticker",
                               auto_adjust=True, threads=True, progress=False)
        except Exception as e:
            print(f"[retry {attempt + 1}] download failed: {e}")
            time.sleep(5 * (attempt + 1))
    return None


def scan(tickers):
    hits = []
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
            for kind, h in hit.items():
                if h:
                    hits.append((t, kind, h))
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


def line_for(t, kind, h):
    name = t.replace(".NS", "")
    link = f"https://www.tradingview.com/chart/?symbol=NSE:{quote(name)}" if t.endswith(".NS") else None
    label = f'<a href="{link}">{html.escape(name)}</a>' if link else html.escape(t)
    sign = ">" if kind == "golden" else "<"
    return f"• <b>{label}</b> ₹{h['close']:.2f} | {MA_TYPE}{FAST} {h['fast']:.1f} {sign} {MA_TYPE}{SLOW} {h['slow']:.1f} | {h['date']}"


def state_key(t, kind):
    return t if kind == "golden" else f"{t}|death"   # golden keeps the plain key


HEADERS = {"golden": "🟡 <b>GOLDEN CROSS (Daily)</b>", "death": "🔴 <b>DEATH CROSS (Daily)</b>"}



def publish_to_sharesanket(hits):
    """Merge fresh cross signals into Share Sanket without replacing existing alerts."""
    token = os.getenv("SHARE_SANKET_TOKEN", "")
    if not token:
        print("[Share Sanket] SHARE_SANKET_TOKEN is not configured; website feed skipped")
        return

    repo = os.getenv("SHARE_SANKET_REPO", "sharesanket/corporate-actions.yml")
    branch = os.getenv("SHARE_SANKET_BRANCH", "main")
    api_url = f"https://api.github.com/repos/{repo}/contents/alerts.json"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).isoformat()

    # Each signal ID is stable, so reruns cannot add the same stock/cross/date twice.
    new_alerts = []
    for ticker, kind, hit in hits:
        symbol = ticker.replace(".NS", "")
        signal_id = f"ema-{kind}-{symbol}-{hit['date']}".lower()
        tradingview = f"https://www.tradingview.com/chart/?symbol=NSE:{quote(symbol)}"
        is_golden = kind == "golden"
        new_alerts.append({
            "id": signal_id,
            "time": now,
            "folder": "emas",
            "subfolder": kind,
            "symbol": symbol,
            "company": symbol,
            "title": "Golden Cross (Daily)" if is_golden else "Death Cross (Daily)",
            "price": round(hit["close"], 2),
            "change_pct": None,
            "details": [
                {"label": "Signal date", "value": hit["date"], "source": "Yahoo Finance"},
                {"label": f"{MA_TYPE}{FAST}", "value": round(hit["fast"], 4), "source": "Yahoo Finance"},
                {"label": f"{MA_TYPE}{SLOW}", "value": round(hit["slow"], 4), "source": "Yahoo Finance"},
                {"label": "Timeframe", "value": "Daily", "source": "Scanner"},
                {"label": "Signal definition",
                 "value": f"{MA_TYPE}{FAST} {'above' if is_golden else 'below'} {MA_TYPE}{SLOW}",
                 "source": "Scanner"},
            ],
            "link": tradingview,
        })

    if not new_alerts:
        print("[Share Sanket] No fresh EMA cross signals to publish")
        return

    for attempt in range(3):
        try:
            current = requests.get(api_url, headers=headers, params={"ref": branch}, timeout=30)
            current.raise_for_status()
            remote = current.json()
            data = json.loads(base64.b64decode(remote["content"]).decode("utf-8"))
            if not isinstance(data.get("alerts"), list):
                raise ValueError("alerts.json does not contain an alerts list")

            known_ids = {str(item.get("id")) for item in data["alerts"]}
            additions = [item for item in new_alerts if item["id"] not in known_ids]
            if not additions:
                print("[Share Sanket] All detected signals are already published")
                return

            data["alerts"] = additions + data["alerts"]
            data["updated"] = now
            encoded = base64.b64encode(
                json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            ).decode("ascii")
            saved = requests.put(
                api_url,
                headers=headers,
                json={
                    "message": f"Publish {len(additions)} fresh EMA cross alert(s)",
                    "content": encoded,
                    "sha": remote["sha"],
                    "branch": branch,
                },
                timeout=30,
            )
            if saved.status_code in (409, 422) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            saved.raise_for_status()
            print(f"[Share Sanket] Published {len(additions)} fresh EMA cross alert(s)")
            return
        except Exception as exc:
            if attempt == 2:
                print(f"[Share Sanket] Publish failed: {exc}")
            else:
                print(f"[Share Sanket] Retry {attempt + 1}: {exc}")
                time.sleep(2 * (attempt + 1))



def main():
    if not TOKEN or not CHAT_ID:
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    if "--test" in sys.argv:
        send_telegram("✅ Golden/Death Cross bot is connected.")
        return

    universe = load_universe()
    print(f"Scanning {len(universe)} tickers")
    state = load_state()
    hits = scan(universe)
    publish_to_sharesanket(hits)

    fresh = [(t, k, h) for t, k, h in hits if state.get(state_key(t, k), "") < h["date"]]
    print(f"{len(hits)} crosses found, {len(fresh)} new")

    for kind in ("golden", "death"):
        items = sorted((t, h) for t, k, h in fresh if k == kind)
        for i in range(0, len(items), 15):
            chunk = items[i:i + 15]
            msg = f"{HEADERS[kind]} — {len(items)} new\n" + "\n".join(line_for(t, kind, h) for t, h in chunk)
            try:
                send_telegram(msg)
            except Exception as e:
                print(f"[telegram error] {e}")  # not saved -> retried next run
                continue
            for t, h in chunk:
                state[state_key(t, kind)] = h["date"]
            time.sleep(1.5)

    new_text = json.dumps(state, indent=2, sort_keys=True)
    if not STATE_FILE.exists() or STATE_FILE.read_text() != new_text:
        STATE_FILE.write_text(new_text)


if __name__ == "__main__":
    main()
