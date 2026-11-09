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
