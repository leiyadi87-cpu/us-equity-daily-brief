"""Session cut-off and overnight data.

The brief is a PRE-MARKET brief. It must report:
  1. the last COMPLETED US session (full regular-hours close), never a half-finished day;
  2. what happened after that close: US after-hours / pre-market moves, the Asian session,
     Europe so far, US index futures, rates, FX and crypto, each with its own timestamp.

Everything here is written so that the brief stays correct whatever time GitHub actually
starts the job (scheduled runs can start hours late).
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
US_OPEN, US_CLOSE = time(9, 30), time(16, 0)
CLOSE_BUFFER = time(16, 15)          # daily bars are final a few minutes after the bell
PRE_START, POST_END = time(4, 0), time(20, 0)


def now_et() -> datetime:
    return datetime.now(ET)


def _dates(index) -> list[date]:
    return [pd.Timestamp(ts).date() for ts in index]


# ------------------------------------------------------------ completed-session cut-off
def last_completed_session(ref: pd.DataFrame, now: datetime | None = None) -> date | None:
    """Latest date in `ref` (a US equity daily series, e.g. SPY) whose regular session has
    closed. Holidays and weekends are handled because only real bar dates are considered."""
    now = (now or now_et()).astimezone(ET)
    for d in reversed(_dates(ref.index)):
        if d < now.date() or (d == now.date() and now.time() >= CLOSE_BUFFER):
            return d
    return None


def cut_to_session(prices: dict[str, pd.DataFrame], session: date) -> dict[str, pd.DataFrame]:
    """Drop any bar dated after `session` (e.g. today's half-finished bar), for every series
    — equities, ETFs, indices and futures alike — so all numbers describe the same day."""
    out = {}
    for t, df in prices.items():
        keep = [d <= session for d in _dates(df.index)]
        cut = df[keep]
        if len(cut):
            out[t] = cut
    return out


def market_phase(now: datetime | None = None) -> str:
    """'pre' / 'regular' / 'post' / 'closed' for the US market right now (weekday logic only)."""
    now = (now or now_et()).astimezone(ET)
    if now.weekday() >= 5:
        return "closed"
    t = now.time()
    if PRE_START <= t < US_OPEN:
        return "pre"
    if US_OPEN <= t < US_CLOSE:
        return "regular"
    if US_CLOSE <= t < POST_END:
        return "post"
    return "closed"


# ------------------------------------------------------------ US extended hours
def _phase_of(ts: datetime, session: date) -> str:
    t = ts.time()
    if ts.date() == session and US_CLOSE <= t < POST_END:
        return "post"
    if PRE_START <= t < US_OPEN:
        return "pre"
    if US_OPEN <= t < US_CLOSE:
        return "regular"
    return "post"


def extended_moves(intraday: dict[str, pd.DataFrame], closes: dict[str, float], session: date) -> dict[str, dict]:
    """From 5-minute bars that include pre/post-market, measure each ticker against its
    official regular-session close on `session`:
      ah_*     = last trade of that day's after-hours session (16:00–20:00 ET)
      latest_* = most recent trade after the close (after-hours, next pre-market, or later)."""
    out = {}
    close_dt = datetime.combine(session, US_CLOSE, ET)
    post_end = datetime.combine(session, POST_END, ET)
    for t, df in intraday.items():
        c = closes.get(t)
        if not c or df is None or df.empty or "Close" not in df:
            continue
        s = df["Close"].dropna()
        if s.empty:
            continue
        idx = s.index.tz_convert(ET) if s.index.tz is not None else s.index.tz_localize(ET)
        s = pd.Series(s.values, index=idx)
        after = s[s.index >= close_dt]
        if after.empty:
            continue
        ah = after[after.index < post_end]
        last_ts = after.index[-1].to_pydatetime()
        out[t] = {
            "ah_price": float(ah.iloc[-1]) if len(ah) else math.nan,
            "ah_pct": float(ah.iloc[-1] / c - 1) if len(ah) else math.nan,
            "latest_price": float(after.iloc[-1]),
            "latest_pct": float(after.iloc[-1] / c - 1),
            "latest_time": last_ts.strftime("%m-%d %H:%M"),
            "latest_phase": _phase_of(last_ts, session),
        }
    return out


def fetch_intraday(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """5-minute bars for the last few days including pre/post-market (Yahoo Finance)."""
    import yfinance as yf

    if not tickers:
        return {}
    raw = yf.download(tickers, period="5d", interval="5m", prepost=True, auto_adjust=False,
                      group_by="ticker", progress=False, threads=True)
    out = {}
    for t in tickers:
        try:
            df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        if "Close" in df and df["Close"].notna().any():
            out[t] = df[["Close"]].dropna()
    return out


# ------------------------------------------------------------ global markets
def _status(m: dict, bar_date: date, now: datetime) -> tuple[str, str]:
    """(status key, human label) for one market quote."""
    if m.get("close") == "24h":
        if (now.date() - bar_date).days >= 2:          # weekend: futures/FX not trading
            return "prior", f"休市（最近报价 {bar_date:%m-%d}）"
        return "live", "实时"
    tz = ZoneInfo(m["tz"])
    local = now.astimezone(tz)
    hh, mm = map(int, m["close"].split(":"))
    if bar_date == local.date():
        if local.time() >= time(hh, mm):
            return "closed", "今日已收盘"
        return "trading", "交易中"
    return "prior", f"休市（最近收盘 {bar_date:%m-%d}）"


def fetch_global(markets: list[dict], now: datetime | None = None) -> list[dict]:
    """Latest level and change for Asian/European indices, US futures, rates, FX, crypto.
    Change is measured against the previous daily close of the same instrument."""
    import yfinance as yf

    now = (now or now_et()).astimezone(ET)
    tickers = [m["ticker"] for m in markets]
    raw = yf.download(tickers, period="15d", interval="1d", auto_adjust=False,
                      group_by="ticker", progress=False, threads=True)
    rows = []
    for m in markets:
        try:
            df = raw[m["ticker"]] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        s = df["Close"].dropna() if "Close" in df else pd.Series(dtype=float)
        if len(s) < 2:
            continue
        last, prev = float(s.iloc[-1]), float(s.iloc[-2])
        bar_date = pd.Timestamp(s.index[-1]).date()
        key, label = _status(m, bar_date, now)
        if key in ("live", "trading"):
            label += f"（截至 {now:%m-%d %H:%M} ET）"
        row = {"ticker": m["ticker"], "name": m["name"], "region": m["region"], "last": last,
               "status": key, "status_label": label, "date": bar_date.isoformat()}
        if m.get("kind") == "yield":
            row["chg_bp"] = (last - prev) * 100      # Yahoo quotes yields in percent
            row["pct"] = math.nan
        else:
            row["pct"] = last / prev - 1
        rows.append(row)
    return rows


def build(prices: dict[str, pd.DataFrame], session: date, cfg: dict) -> dict:
    """All overnight data, never raising: a failure here must not stop the brief."""
    ocfg = cfg.get("overnight", {})
    now = now_et()
    res = {"generated_et": now.strftime("%Y-%m-%d %H:%M"), "phase": market_phase(now),
           "session": session.isoformat(), "global": [], "extended": {}, "errors": []}
    try:
        res["global"] = fetch_global(ocfg.get("markets", []), now)
    except Exception as e:
        log.warning("Global markets fetch failed: %s", e)
        res["errors"].append("global")
    try:
        eq = [t for t in prices if not t.startswith("^") and "=F" not in t]
        closes = {t: float(prices[t]["Close"].iloc[-1]) for t in eq
                  if pd.Timestamp(prices[t].index[-1]).date() == session}
        res["extended"] = extended_moves(fetch_intraday(eq), closes, session)
    except Exception as e:
        log.warning("Extended-hours fetch failed: %s", e)
        res["errors"].append("extended")
    return res
