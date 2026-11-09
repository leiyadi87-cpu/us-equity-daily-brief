"""Quant layer: per-ticker metrics, technical signals and group (sector) aggregates."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0)


def _ret(c: pd.Series, n: int) -> float:
    return float(c.iloc[-1] / c.iloc[-1 - n] - 1) if len(c) > n else float("nan")


def compute_metrics(df: pd.DataFrame) -> dict | None:
    c = df["Close"].dropna()
    if len(c) < 30:
        return None
    v = df["Volume"].reindex(c.index).fillna(0)
    rets = c.pct_change()
    last = float(c.iloc[-1])

    this_year = c.index[-1].year
    prior = c[c.index.year < this_year]
    ytd_base = prior.iloc[-1] if len(prior) else c[c.index.year == this_year].iloc[0]

    sma50 = c.rolling(50).mean()
    sma200 = c.rolling(200).mean()
    vol20 = float(rets.iloc[-21:-1].std())            # prior 20 days, excludes today
    avg_vol20 = float(v.iloc[-21:-1].mean())
    window = c.iloc[-TRADING_DAYS:]

    # 50/200 cross within the last 5 sessions
    cross = None
    if sma200.notna().sum() >= 6:
        diff = (sma50 - sma200).dropna().iloc[-6:]
        if (diff.iloc[0] < 0) and (diff.iloc[-1] > 0):
            cross = "golden"
        elif (diff.iloc[0] > 0) and (diff.iloc[-1] < 0):
            cross = "death"

    def pct_vs(ma):
        x = ma.iloc[-1]
        return float(last / x - 1) if pd.notna(x) else float("nan")

    prev_vs200 = (c.iloc[-2] / sma200.iloc[-2] - 1) if pd.notna(sma200.iloc[-2]) else float("nan")

    return {
        "as_of": c.index[-1].strftime("%Y-%m-%d"),
        "close": last,
        "ret_1d": _ret(c, 1), "ret_5d": _ret(c, 5), "ret_1m": _ret(c, 21),
        "ret_ytd": float(last / ytd_base - 1),
        "rsi14": float(rsi(c).iloc[-1]),
        "vs_sma50": pct_vs(sma50), "vs_sma200": pct_vs(sma200),
        "prev_vs_sma200": float(prev_vs200),
        "z_1d": float(rets.iloc[-1] / vol20) if vol20 > 0 else float("nan"),
        "ann_vol20": float(rets.iloc[-20:].std() * math.sqrt(TRADING_DAYS)),
        "vol_ratio": float(v.iloc[-1] / avg_vol20) if avg_vol20 > 0 else float("nan"),
        "hi52": float(window.max()), "lo52": float(window.min()),
        "cross": cross,
    }


LABELS = {
    "zh": {
        "big_move": "{t} 单日{dir} {r:+.1%}，为 {z:.1f} 倍标准差的异常波动",
        "volume": "{t} 成交量放大至 20 日均量的 {x:.1f} 倍",
        "rsi_high": "{t} RSI {v:.0f}，进入超买区",
        "rsi_low": "{t} RSI {v:.0f}，进入超卖区",
        "hi52": "{t} 接近/创出 52 周新高",
        "lo52": "{t} 接近/创出 52 周新低",
        "golden": "{t} 50 日均线上穿 200 日均线（金叉）",
        "death": "{t} 50 日均线下穿 200 日均线（死叉）",
        "up200": "{t} 收盘站上 200 日均线",
        "dn200": "{t} 收盘跌破 200 日均线",
        "up": "大涨", "down": "大跌",
    },
    "en": {
        "big_move": "{t} {dir} {r:+.1%} — a {z:.1f}σ move",
        "volume": "{t} volume {x:.1f}x its 20-day average",
        "rsi_high": "{t} RSI {v:.0f} — overbought",
        "rsi_low": "{t} RSI {v:.0f} — oversold",
        "hi52": "{t} at/near a 52-week high",
        "lo52": "{t} at/near a 52-week low",
        "golden": "{t} golden cross (50DMA above 200DMA)",
        "death": "{t} death cross (50DMA below 200DMA)",
        "up200": "{t} closed back above its 200DMA",
        "dn200": "{t} closed below its 200DMA",
        "up": "jumped", "down": "fell",
    },
}


def detect_signals(ticker: str, m: dict, cfg: dict, lang: str = "zh", label: str | None = None) -> list[dict]:
    s, L, t = cfg["signals"], LABELS[lang], label or ticker
    out = []

    def add(kind, score, **kw):
        out.append({"ticker": ticker, "type": kind, "score": float(score), "text": L[kind].format(t=t, **kw)})

    if abs(m["z_1d"]) >= s["big_move_sigma"]:
        add("big_move", abs(m["z_1d"]), dir=L["up"] if m["ret_1d"] > 0 else L["down"], r=m["ret_1d"], z=abs(m["z_1d"]))
    if m["vol_ratio"] >= s["volume_spike"]:
        add("volume", m["vol_ratio"], x=m["vol_ratio"])
    if m["rsi14"] >= s["rsi_high"]:
        add("rsi_high", (m["rsi14"] - s["rsi_high"]) / 10 + 1, v=m["rsi14"])
    elif m["rsi14"] <= s["rsi_low"]:
        add("rsi_low", (s["rsi_low"] - m["rsi14"]) / 10 + 1, v=m["rsi14"])
    if m["close"] >= m["hi52"] * (1 - s["near_52w_pct"]):
        add("hi52", 1.5)
    elif m["close"] <= m["lo52"] * (1 + s["near_52w_pct"]):
        add("lo52", 1.5)
    if m["cross"]:
        add(m["cross"], 2.0)
    if not math.isnan(m["vs_sma200"]) and not math.isnan(m["prev_vs_sma200"]):
        if m["prev_vs_sma200"] < 0 <= m["vs_sma200"]:
            add("up200", 1.8)
        elif m["prev_vs_sma200"] > 0 >= m["vs_sma200"]:
            add("dn200", 1.8)
    return out


def analyze(prices: dict[str, pd.DataFrame], cfg: dict) -> dict:
    """Returns {'groups': [...], 'signals': [...], 'as_of': str, 'missing': [...]}."""
    lang = cfg.get("language", "zh")
    names = cfg.get("names", {})
    groups, signals, missing, dates = [], [], [], []

    for g in cfg["groups"]:
        rows = []
        for t in g["tickers"]:
            m = compute_metrics(prices[t]) if t in prices else None
            if m is None:
                missing.append(t)
                continue
            label = names.get(t, t)
            rows.append({"ticker": t, "name": label, **m})
            dates.append(m["as_of"])
            signals += detect_signals(t, m, cfg, lang, label if label != t else None)
        etf = None
        if g.get("etf") and g["etf"] in prices:
            em = compute_metrics(prices[g["etf"]])
            if em:
                etf = {"ticker": g["etf"], **em}
        avg = {k: float(np.nanmean([r[k] for r in rows])) if rows else float("nan")
               for k in ("ret_1d", "ret_5d", "ret_1m", "ret_ytd")}
        groups.append({"name": g["name"], "rows": rows, "etf": etf, "avg": avg})

    signals.sort(key=lambda x: x["score"], reverse=True)
    as_of = max(set(dates), key=dates.count) if dates else "n/a"
    return {"groups": groups, "signals": signals, "as_of": as_of, "missing": missing}


def top_movers(result: dict, n: int = 5) -> tuple[list[dict], list[dict]]:
    rows = [r for g in result["groups"] for r in g["rows"] if r["ticker"] != "^VIX"]
    rows = sorted(rows, key=lambda r: r["ret_1d"], reverse=True)
    return rows[:n], rows[::-1][:n]
