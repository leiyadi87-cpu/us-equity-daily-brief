import math

import numpy as np
import pandas as pd
import yaml
from pathlib import Path

from src import analysis, demo, llm, report

CFG = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8"))


def _series(close, volume=None):
    idx = pd.bdate_range("2025-01-02", periods=len(close))
    return pd.DataFrame({"Close": close, "Volume": volume if volume is not None else np.full(len(close), 1e6)}, index=idx)


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.linspace(100, 200, 60))
    down = pd.Series(np.linspace(200, 100, 60))
    assert analysis.rsi(up).iloc[-1] > 95
    assert analysis.rsi(down).iloc[-1] < 5
    rnd = pd.Series(100 * np.exp(np.cumsum(np.random.default_rng(0).normal(0, 0.01, 300))))
    r = analysis.rsi(rnd).dropna()
    assert r.between(0, 100).all()


def test_returns_and_ytd():
    close = np.full(260, 100.0)
    close[-1] = 110.0
    m = analysis.compute_metrics(_series(close))
    assert math.isclose(m["ret_1d"], 0.10, rel_tol=1e-9)
    assert math.isclose(m["ret_5d"], 0.10, rel_tol=1e-9)


def test_big_move_and_volume_signals():
    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 260)))
    close[-1] = close[-2] * 1.08          # ~8 sigma
    vol = np.full(260, 1e6)
    vol[-1] = 3e6
    m = analysis.compute_metrics(_series(close, vol))
    kinds = {s["type"] for s in analysis.detect_signals("XYZ", m, CFG)}
    assert {"big_move", "volume"} <= kinds


def test_no_false_signal_on_quiet_day():
    rng = np.random.default_rng(2)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 260)))
    close[-1] = close[-2] * 1.001
    m = analysis.compute_metrics(_series(close))
    kinds = {s["type"] for s in analysis.detect_signals("XYZ", m, CFG)}
    assert "big_move" not in kinds and "volume" not in kinds


def test_end_to_end_demo_render():
    tickers = [t for g in CFG["groups"] for t in g["tickers"]] + [g["etf"] for g in CFG["groups"] if g.get("etf")]
    result = analysis.analyze(demo.demo_prices(tickers), CFG)
    assert not result["missing"]
    assert any(s["ticker"] == "NVDA" and s["type"] == "big_move" for s in result["signals"])
    md = llm.rule_based(result, "zh")
    subject, html, _ = report.render(result, demo.demo_news(), md, "rule-based", CFG, demo=True)
    assert "NVDA" in html and "DEMO DATA" in html and subject.startswith("每日美股研究简报")
    assert "Data as of close" in llm.build_payload(result, demo.demo_news())
