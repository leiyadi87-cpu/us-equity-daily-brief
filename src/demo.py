"""Synthetic prices + headlines so the whole pipeline can run offline (`python main.py --demo`)."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

BASE = {"SPY": 590, "QQQ": 510, "^VIX": 16, "NVDA": 180, "MSFT": 480, "AAPL": 240, "GOOGL": 210,
        "AMZN": 225, "META": 700, "TSLA": 380, "GC=F": 2900, "SI=F": 33, "PL=F": 1000, "HG=F": 4.6,
        "CL=F": 70, "NG=F": 3.4}


def demo_prices(tickers: list[str], seed: int = 7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.offsets.BDay(1), periods=260)
    out = {}
    for t in tickers:
        vol = 0.06 if t == "^VIX" else rng.uniform(0.010, 0.025)
        r = rng.normal(0.0004, vol, len(idx))
        if t == "NVDA":
            r[-1] = 0.071                      # earnings-style jump
        if t == "MRNA":
            r[-1] = -0.088
        if t == "GC=F":
            r[-30:] += 0.004                   # steady rally -> overbought
        close = BASE.get(t, rng.uniform(40, 400)) * np.exp(np.cumsum(r))
        volume = rng.lognormal(16, 0.25, len(idx))
        if t in ("NVDA", "MRNA"):
            volume[-1] *= 3.2
        out[t] = pd.DataFrame({"Close": close, "Volume": 0 if t == "^VIX" else volume}, index=idx)
    return out


def demo_news() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"ticker": "NVDA", "title": "[DEMO] Nvidia beats revenue estimates on data-center demand", "url": None, "publisher": "Demo Wire", "time": now},
        {"ticker": "MRNA", "title": "[DEMO] Moderna cuts full-year sales guidance", "url": None, "publisher": "Demo Wire", "time": now},
        {"ticker": "GC=F", "title": "[DEMO] Gold extends rally as real yields slip", "url": None, "publisher": "Demo Wire", "time": now},
        {"ticker": "MARKET", "title": "[DEMO] CPI report due Wednesday; Fed speakers on deck", "url": None, "publisher": "Demo Wire", "time": now},
    ]


def demo_overnight(session) -> dict:
    """Synthetic overnight panel matching overnight.build()'s shape."""
    def g(t, name, region, pct, status="closed", label="今日已收盘", last=100.0, **kw):
        return {"ticker": t, "name": name, "region": region, "last": last, "pct": pct,
                "status": status, "status_label": label, "date": str(session), **kw}
    return {
        "generated_et": f"{session} 06:05 (demo)", "phase": "pre", "session": str(session), "errors": [],
        "global": [
            g("^N225", "日经225 Nikkei", "亚洲 Asia", 0.012, last=48210.5),
            g("^HSI", "恒生指数 Hang Seng", "亚洲 Asia", -0.008, last=25890.1),
            g("000001.SS", "上证指数 Shanghai", "亚洲 Asia", 0.003, last=3980.2),
            g("^GDAXI", "德国 DAX", "欧洲 Europe", 0.004, "trading", "交易中（截至 06:05 ET）", last=24100.0),
            g("ES=F", "标普500期货 ES", "美股期货 US futures", 0.0035, "live", "实时（截至 06:05 ET）", last=6720.25),
            g("NQ=F", "纳指100期货 NQ", "美股期货 US futures", 0.0061, "live", "实时（截至 06:05 ET）", last=24510.0),
            {"ticker": "^TNX", "name": "美国10年期国债收益率", "region": "利率汇率 Rates & FX", "last": 4.31,
             "pct": float("nan"), "chg_bp": -2.1, "status": "prior", "status_label": "最近收盘", "date": str(session)},
        ],
        "extended": {
            "SPY": {"ah_price": 591.2, "ah_pct": 0.001, "latest_price": 592.0, "latest_pct": 0.0024,
                    "latest_time": "06:00", "latest_phase": "pre"},
            "NVDA": {"ah_price": 190.5, "ah_pct": 0.042, "latest_price": 191.8, "latest_pct": 0.049,
                     "latest_time": "06:00", "latest_phase": "pre"},
        },
    }
