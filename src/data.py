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
    df = df[["Close", "Volume"]].dropna(subset=["Close"])
    return df if len(df) else None


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
    return _refresh_stale(out, start, end)


def latest_session(ticker: str = "SPY") -> pd.Timestamp | None:
    """Independent check of the latest daily bar, via Ticker.history instead of download."""
    import yfinance as yf
    try:
        h = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=True)
        return h.index[-1].tz_localize(None).normalize() if len(h) else None
    except Exception as e:
        log.warning("latest_session check failed: %s", e)
        return None


def _refresh_stale(out: dict[str, pd.DataFrame], start: str, end: str) -> dict[str, pd.DataFrame]:
    """If some series end before the latest session, re-fetch them one by one."""
    import yfinance as yf

    ref = latest_session()
    if ref is None:
        return out
    stale = [t for t, df in out.items()
             if df.index[-1].tz_localize(None).normalize() < ref and "=F" not in t and not t.startswith("^")]
    if not stale:
        return out
    log.warning("%d series end before %s — re-fetching individually", len(stale), ref.date())
    time.sleep(3)
    fixed = 0
    for t in stale:
        try:
            h = yf.Ticker(t).history(start=start, end=end, interval="1d", auto_adjust=True)
            if len(h) and h.index[-1].tz_localize(None).normalize() >= ref:
                h.index = h.index.tz_localize(None)
                out[t] = h[["Close", "Volume"]].dropna(subset=["Close"])
                fixed += 1
        except Exception as e:
            log.warning("re-fetch %s failed: %s", t, e)
    log.info("Re-fetched %d/%d stale series", fixed, len(stale))
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
