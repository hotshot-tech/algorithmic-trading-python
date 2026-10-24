"""Post MT5 price reactions to this week's economic events to Discord.

Runs on a Windows PC with MetaTrader 5 open and logged in. For every event the
Market Brief bots covered this week (all High-impact events for the 8 major
currencies, plus Medium-impact USD and GBP events), it measures how the main
pairs moved 15 and 60 minutes after the release, using your broker's M1 bars,
and posts the table to Discord.

Usage:
    python news_reaction.py              # post to Discord
    python news_reaction.py --dry-run    # print only
"""

import argparse
import configparser
import datetime as dt
import json
import os
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
IRISH = ZoneInfo("Europe/Dublin")
NEW_YORK = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

CURRENCIES = {"USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"}
MEDIUM_CURRENCIES = {"USD", "GBP"}

# Pairs to measure for each event currency (the most relevant ones the server trades)
PAIRS_FOR = {
    "USD": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
    "EUR": ["EURUSD", "EURGBP", "EURJPY"],
    "GBP": ["GBPUSD", "EURGBP", "GBPJPY"],
    "JPY": ["USDJPY", "EURJPY", "GBPJPY"],
    "AUD": ["AUDUSD", "AUDNZD", "EURAUD"],
    "NZD": ["NZDUSD", "AUDNZD"],
    "CAD": ["USDCAD", "EURCAD"],
    "CHF": ["USDCHF", "EURCHF", "GBPCHF"],
}

WINDOWS_MIN = (15, 60)
DISCORD_LIMIT = 1900
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")


def load_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)
    return config


def fetch_events(now_utc):
    """Covered events from this week's calendar whose 60-minute window has finished."""
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "market-brief/1.0"})
    feed = json.load(urllib.request.urlopen(req, timeout=20))

    grouped = {}
    for e in feed:
        impact, ccy = e.get("impact"), e.get("country")
        if ccy not in CURRENCIES:
            continue
        if not (impact == "High" or (impact == "Medium" and ccy in MEDIUM_CURRENCIES)):
            continue
        t = dt.datetime.fromisoformat(e["date"]).astimezone(UTC)
        if t + dt.timedelta(minutes=max(WINDOWS_MIN)) > now_utc:
            continue
        # Lines of the same release (CPI m/m + y/y, etc.) are one event
        key = (t, ccy)
        event = grouped.setdefault(key, {"time": t, "currency": ccy, "impact": impact, "titles": []})
        event["titles"].append(e["title"])
        if impact == "High":
            event["impact"] = "High"
    return sorted(grouped.values(), key=lambda ev: ev["time"])


def market_is_closed(now_utc):
    """FX is closed from Friday 17:00 to Sunday 17:00 New York time."""
    ny = now_utc.astimezone(NEW_YORK)
    if ny.weekday() == 5:
        return True
    if ny.weekday() == 4 and ny.hour >= 17:
        return True
    if ny.weekday() == 6 and ny.hour < 17:
        return True
    return False


def detect_server_offset(symbol, now_utc):
    """Broker server time minus UTC, in whole hours.

    MT5 bar and tick times are in broker server time, which is usually UTC+2/+3.
    When the market is open, compare the latest tick with the clock. When it is
    closed, compare the last bar of the week with the known Friday 17:00 New York close.
    """
    if not market_is_closed(now_utc):
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"No tick for {symbol}; cannot detect server time offset")
        return round((tick.time - now_utc.timestamp()) / 3600)

    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No bars for {symbol}; cannot detect server time offset")
    ny = now_utc.astimezone(NEW_YORK)
    days_back = (ny.weekday() - 4) % 7
    if ny.weekday() == 4 and ny.hour < 17:
        days_back = 7
    friday = (ny - dt.timedelta(days=days_back)).date()
    close_utc = dt.datetime(friday.year, friday.month, friday.day, 17, tzinfo=NEW_YORK).astimezone(UTC)
    return round((int(rates[0]["time"]) - close_utc.timestamp()) / 3600)


def pip_size(info):
    if info.digits in (3, 5):
        return info.point * 10
    return info.point


def price_at(bars, server_ts):
    """Close of the last M1 bar that finished at or before server_ts."""
    price = None
    for bar in bars:
        if int(bar["time"]) + 60 <= server_ts:
            price = float(bar["close"])
        else:
            break
    return price


def measure(symbol, event_utc, offset_h):
    info = mt5.symbol_info(symbol)
    if info is None:
        return None
    mt5.symbol_select(symbol, True)

    release = int(event_utc.timestamp()) + offset_h * 3600
    # Fetch a wide window and filter on bar times ourselves, so this works whether
    # the terminal reads the range as UTC or as server time.
    start = dt.datetime.fromtimestamp(release - 6 * 3600, tz=UTC)
    end = dt.datetime.fromtimestamp(release + 6 * 3600, tz=UTC)
    bars = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, start, end)
    if bars is None or len(bars) == 0:
        return None
    bars = [b for b in bars if release - 3600 <= int(b["time"]) <= release + max(WINDOWS_MIN) * 60]

    base = price_at(bars, release)
    if base is None:
        return None

    moves = {}
    for minutes in WINDOWS_MIN:
        after = price_at(bars, release + minutes * 60)
        moves[minutes] = None if after is None else after - base

    in_window = [b for b in bars if release <= int(b["time"]) < release + max(WINDOWS_MIN) * 60]
    swing = None
    if in_window:
        swing = max(float(b["high"]) for b in in_window) - min(float(b["low"]) for b in in_window)

    return {"moves": moves, "swing": swing, "pip": pip_size(info)}


def format_move(value, symbol, pip):
    if value is None:
        return "n/a"
    if symbol.startswith("XAU"):
        return f"{value:+.1f}$"
    return f"{value / pip:+.1f}p"


def display_pair(base_symbol):
    return f"{base_symbol[:3]}/{base_symbol[3:]}"


def build_blocks(events, suffix, offset_h):
    blocks = []
    for ev in events:
        rows = []
        for pair in PAIRS_FOR[ev["currency"]]:
            symbol = pair + suffix
            result = measure(symbol, ev["time"], offset_h)
            if result is None:
                rows.append(f"{display_pair(pair):<8} no data")
                continue
            cells = [f"{m}m {format_move(result['moves'][m], pair, result['pip']):>8}" for m in WINDOWS_MIN]
            swing = format_move(result["swing"], pair, result["pip"]).lstrip("+")
            rows.append(f"{display_pair(pair):<8} " + "  ".join(cells) + f"  range {swing:>7}")

        when = ev["time"].astimezone(IRISH).strftime("%a %H:%M")
        tag = "🔴" if ev["impact"] == "High" else "🟠"
        title = " + ".join(dict.fromkeys(ev["titles"]))
        blocks.append(f"{tag} **{when} · {ev['currency']} · {title}**\n```\n" + "\n".join(rows) + "\n```")
    return blocks


def build_messages(events, blocks, now_utc):
    if events:
        first = events[0]["time"].astimezone(IRISH)
        last = events[-1]["time"].astimezone(IRISH)
        span = f"{first:%d}–{last:%d %b}" if first.month == last.month else f"{first:%d %b}–{last:%d %b}"
    else:
        span = now_utc.astimezone(IRISH).strftime("week of %d %b")

    header = (
        f"**📊 MT5 price reactions: {span}**\n"
        "*Move from just before each release to 15 and 60 min after, plus the high–low range in that hour. "
        "p = pips, $ = gold dollars. Irish time.*"
    )
    footer = "-# 🤖 Broker MT5 prices, measured automatically. Spreads and slippage around releases are not included. Not financial advice."

    if not blocks:
        return [f"{header}\n\nNo covered events this week.\n\n{footer}"]

    messages, current = [], header
    for block in blocks:
        if len(current) + len(block) + 2 > DISCORD_LIMIT:
            messages.append(current)
            current = block
        else:
            current += "\n\n" + block
    if len(current) + len(footer) + 2 > DISCORD_LIMIT:
        messages.append(current)
        current = footer
    else:
        current += "\n\n" + footer
    messages.append(current)
    return messages


def post_to_discord(webhook_url, text):
    body = json.dumps({
        "username": "Market Brief",
        "content": text,
        "flags": 4,  # suppress link previews
        "allowed_mentions": {"parse": []},
    }).encode()
    req = urllib.request.Request(webhook_url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "User-Agent": "market-brief/1.0",
    })
    urllib.request.urlopen(req, timeout=20)


def main():
    config = load_config()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the messages instead of posting")
    parser.add_argument("--suffix", default=config.get("mt5", "symbol_suffix", fallback=""),
                        help="broker symbol suffix, e.g. .a or m (default from config.ini)")
    parser.add_argument("--server-offset", type=int,
                        default=config.getint("mt5", "server_utc_offset", fallback=None),
                        help="broker server time minus UTC in hours (default: auto-detect)")
    args = parser.parse_args()

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL") or config.get("discord", "webhook_url", fallback="")
    if not args.dry_run and not webhook_url:
        sys.exit("No Discord webhook: set DISCORD_WEBHOOK_URL or webhook_url in config.ini")

    terminal_path = config.get("mt5", "terminal_path", fallback="")
    ok = mt5.initialize(terminal_path) if terminal_path else mt5.initialize()
    if not ok:
        sys.exit(f"MT5 initialize() failed: {mt5.last_error()}. Is MetaTrader 5 open and logged in?")

    try:
        now_utc = dt.datetime.now(UTC)
        offset_h = args.server_offset
        if offset_h is None:
            offset_h = detect_server_offset("EURUSD" + args.suffix, now_utc)
        print(f"Broker server time offset: UTC{offset_h:+d}")

        events = fetch_events(now_utc)
        print(f"{len(events)} covered events this week")
        blocks = build_blocks(events, args.suffix, offset_h)
        messages = build_messages(events, blocks, now_utc)
    finally:
        mt5.shutdown()

    for i, text in enumerate(messages):
        if args.dry_run:
            print(text, end="\n\n")
            continue
        post_to_discord(webhook_url, text)
        print(f"posted message {i + 1}/{len(messages)}")
        time.sleep(1)


if __name__ == "__main__":
    main()
