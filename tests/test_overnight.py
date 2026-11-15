import math
from datetime import date, datetime

import numpy as np
import pandas as pd

from src import overnight as ov

ET = ov.ET


def _daily(dates):
    idx = pd.to_datetime(dates)
    return pd.DataFrame({"Close": np.arange(len(dates), dtype=float) + 100, "Volume": 1e6}, index=idx)


def test_session_drops_half_finished_day():
    df = _daily(["2026-11-12", "2026-11-13", "2026-11-16"])
    # Monday 15:00 ET: today's bar is in progress -> last completed session is Friday
    assert ov.last_completed_session(df, datetime(2026, 11, 16, 15, 0, tzinfo=ET)) == date(2026, 11, 13)
    # Monday 16:30 ET: today is complete
    assert ov.last_completed_session(df, datetime(2026, 11, 16, 16, 30, tzinfo=ET)) == date(2026, 11, 16)
    # Tuesday 06:00 ET (pre-market): Monday is the last completed session
    df2 = _daily(["2026-11-13", "2026-11-16"])
    assert ov.last_completed_session(df2, datetime(2026, 11, 17, 6, 0, tzinfo=ET)) == date(2026, 11, 16)


def test_cut_to_session_applies_to_every_series():
    prices = {"SPY": _daily(["2026-11-13", "2026-11-16"]), "GC=F": _daily(["2026-11-13", "2026-11-16", "2026-11-17"])}
    cut = ov.cut_to_session(prices, date(2026, 11, 16))
    assert all(pd.Timestamp(df.index[-1]).date() == date(2026, 11, 16) for df in cut.values())


def test_extended_moves_vs_official_close():
    idx = pd.DatetimeIndex([
        datetime(2026, 11, 16, 15, 55), datetime(2026, 11, 16, 16, 30), datetime(2026, 11, 16, 19, 55),
        datetime(2026, 11, 17, 4, 5), datetime(2026, 11, 17, 6, 0)]).tz_localize(ET)
    bars = {"NVDA": pd.DataFrame({"Close": [100.5, 104.0, 105.0, 106.0, 107.0]}, index=idx)}
    e = ov.extended_moves(bars, {"NVDA": 100.0}, date(2026, 11, 16))["NVDA"]
    assert math.isclose(e["ah_pct"], 0.05)          # last after-hours trade 105 vs close 100
    assert math.isclose(e["latest_pct"], 0.07)      # latest pre-market 107
    assert e["latest_phase"] == "pre" and e["latest_time"] == "11-17 06:00"


def test_status_labels():
    now = datetime(2026, 11, 17, 6, 0, tzinfo=ET)   # = 20:00 Tokyo, 12:00 Berlin
    tokyo = {"tz": "Asia/Tokyo", "close": "15:30"}
    berlin = {"tz": "Europe/Berlin", "close": "17:30"}
    assert ov._status(tokyo, date(2026, 11, 17), now)[0] == "closed"
    assert ov._status(berlin, date(2026, 11, 17), now)[0] == "trading"
    assert ov._status(tokyo, date(2026, 11, 14), now)[0] == "prior"
    assert ov._status({"close": "24h"}, date(2026, 11, 17), now)[0] == "live"


def test_market_phase():
    assert ov.market_phase(datetime(2026, 11, 17, 6, 0, tzinfo=ET)) == "pre"
    assert ov.market_phase(datetime(2026, 11, 17, 12, 0, tzinfo=ET)) == "regular"
    assert ov.market_phase(datetime(2026, 11, 21, 12, 0, tzinfo=ET)) == "closed"
