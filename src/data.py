"""Market data + news fetching (Yahoo Finance via yfinance, optional Finnhub)."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- prices
def _extract(raw: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    try:
        df = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
    except KeyError:
        return None
    if "Close" not in df:
        return None
    df = df[["Close", "Volume"]].dropna(how="all")
    return df if df["Close"].notna().any() else None


def fetch_prices(tickers: list[str], period: str = "1y", retries: int = 3) -> dict[str, pd.DataFrame]:
    """Daily adjusted Close + Volume for each ticker. Retries tickers that failed."""
    import yfinance as yf

    # explicit date range: `period=` has occasionally returned data missing the latest session
    years = int(period.rstrip("y")) if period.endswith("y") else 1
    end = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=366 * years + 10)).strftime("%Y-%m-%d")

    out: dict[str, pd.DataFrame] = {}
    remaining = list(dict.fromkeys(tickers))
    for attempt in range(retries):
        if not remaining:
            break
        raw = yf.download(remaining, start=start, end=end, auto_adjust=True, group_by="ticker",
                          progress=False, threads=True)
        for t in remaining:
            df = _extract(raw, t)
            if df is not None:
                out[t] = df
        remaining = [t for t in remaining if t not in out]
        if remaining and attempt < retries - 1:
            log.warning("Retrying %d tickers: %s", len(remaining), remaining)
            time.sleep(5 * (attempt + 1))
    if remaining:
        log.error("No data for: %s", remaining)
    return _patch_missing_last_close(out)


def _patch_missing_last_close(out: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Yahoo sometimes serves the newest daily bar with Volume but Close=NaN (seen in the
    evening after the close). Fill it from the quote's regularMarketPrice; drop it if unavailable.
    Futures are skipped because their 'last price' is a live, still-trading quote."""
    import yfinance as yf

    patched = []
    for t, df in out.items():
        if pd.isna(df["Close"].iloc[-1]) and "=F" not in t:
            try:
                lp = yf.Ticker(t).fast_info.get("lastPrice")
            except Exception as e:
                lp = None
                log.warning("fast_info failed for %s: %s", t, e)
            if lp and lp > 0:
                df.iloc[-1, df.columns.get_loc("Close")] = float(lp)
                patched.append(t)
        out[t] = df.dropna(subset=["Close"])
    if patched:
        log.info("Filled missing latest close from quotes for %d tickers", len(patched))
    return out


# ---------------------------------------------------------------- news
def _parse_yahoo_item(it: dict) -> dict | None:
    """yfinance has shipped two news formats; handle both."""
    c = it.get("content")
    if isinstance(c, dict):  # newer format
        url = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
        ts = pd.to_datetime(c.get("pubDate"), utc=True, errors="coerce")
        return {"title": c.get("title"), "url": url,
                "publisher": (c.get("provider") or {}).get("displayName", ""),
                "time": None if pd.isna(ts) else ts.to_pydatetime()}
    if it.get("title"):  # older format
        ts = it.get("providerPublishTime")
        return {"title": it["title"], "url": it.get("link"), "publisher": it.get("publisher", ""),
                "time": datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None}
    return None


def fetch_news(tickers: list[str], per_ticker: int = 2, max_age_hours: int = 36) -> list[dict]:
    import yfinance as yf

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items, seen = [], set()
    for t in tickers:
        try:
            raw = yf.Ticker(t).news or []
        except Exception as e:  # network hiccups shouldn't kill the brief
            log.warning("News failed for %s: %s", t, e)
            continue
        n = 0
        for it in raw:
            p = _parse_yahoo_item(it)
            if not p or not p["title"] or p["title"] in seen:
                continue
            if p["time"] and p["time"] < cutoff:
                continue
            p["ticker"] = t
            items.append(p)
            seen.add(p["title"])
            n += 1
            if n >= per_ticker:
                break
    return items


def fetch_market_news(limit: int = 10) -> list[dict]:
    """General market headlines from Finnhub (free key). Skipped if no key."""
    key = os.getenv("FINNHUB_API_KEY")
    if not key:
        return []
    import requests

    try:
        r = requests.get("https://finnhub.io/api/v1/news",
                         params={"category": "general", "token": key}, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.warning("Finnhub failed: %s", e)
        return []
    return [{"ticker": "MARKET", "title": x.get("headline"), "url": x.get("url"),
             "publisher": x.get("source", ""),
             "time": datetime.fromtimestamp(x["datetime"], tz=timezone.utc) if x.get("datetime") else None}
            for x in r.json()[:limit] if x.get("headline")]
