"""Daily US Market Brief — entry point.

    python main.py                 # full run: fetch → analyze → AI summary → email → save
    python main.py --no-email      # build and save the report only
    python main.py --demo          # offline run on synthetic data (no network, no keys)
    python main.py --once-per-day  # skip if today's brief was already sent (used by the schedule)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

import yaml

from src import analysis, data, llm, overnight, report

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("brief")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=ROOT / "config.yaml")
    ap.add_argument("--no-email", action="store_true", help="skip sending email")
    ap.add_argument("--no-ai", action="store_true", help="use the rule-based summary")
    ap.add_argument("--demo", action="store_true", help="synthetic data, offline")
    ap.add_argument("--out", default=ROOT / "reports")
    ap.add_argument("--once-per-day", action="store_true",
                    help="exit quietly if today's brief (US Eastern date) was already sent")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    out = Path(args.out)
    marker = out / f".sent-{overnight.now_et():%Y-%m-%d}"
    if args.once_per_day and marker.exists():
        log.info("Today's brief was already sent (%s) — nothing to do.", marker.name)
        return

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if args.no_ai:
        cfg["llm"]["provider"] = "none"

    tickers = [t for g in cfg["groups"] for t in g["tickers"]]
    tickers += [g["etf"] for g in cfg["groups"] if g.get("etf")]

    # 1) data
    if args.demo:
        from src import demo
        prices, news = demo.demo_prices(tickers), demo.demo_news()
    else:
        log.info("Fetching prices for %d tickers", len(tickers))
        prices = data.fetch_prices(tickers, period=cfg.get("lookback", "1y"))
        stock_tickers = [t for g in cfg["groups"] for t in g["tickers"]]
        news = data.fetch_market_news() + data.fetch_news(
            stock_tickers, cfg["news"]["per_ticker"], cfg["news"]["max_age_hours"])
        log.info("Got %d price series, %d headlines", len(prices), len(news))
    if not prices:
        raise SystemExit("No price data retrieved — aborting.")

    # 1b) only use COMPLETED sessions: drop today's half-finished bar if the US market
    #     hasn't closed yet, so every number is a real close
    if args.demo:
        session = max(pd_date(df.index[-1]) for df in prices.values())
    else:
        ref = prices["SPY"] if "SPY" in prices else next(iter(prices.values()))
        session = overnight.last_completed_session(ref)
        if session is None:
            raise SystemExit("Could not determine the last completed session — aborting.")
        prices = overnight.cut_to_session(prices, session)
    log.info("Last completed US session: %s", session)

    # 2) quant
    result = analysis.analyze(prices, cfg)
    result["session"] = session.isoformat()
    result["ext_move_min"] = cfg.get("overnight", {}).get("ext_move_min", 0.015)

    # 2b) overnight: after-hours / pre-market, Asia, Europe, futures, rates, FX
    if args.demo:
        from src import demo
        result["overnight"] = demo.demo_overnight(session)
    else:
        result["overnight"] = overnight.build(prices, session, cfg)

    # 3) AI
    summary_md, source = llm.summarize(result, news, cfg)
    log.info("Summary written by %s", source)

    # 4) render + save
    subject, html, text = report.render(result, news, summary_md, source, cfg, demo=args.demo)
    out.mkdir(parents=True, exist_ok=True)
    stem = ("demo-" if args.demo else "") + result["as_of"]
    (out / f"{stem}.html").write_text(html, encoding="utf-8")
    (out / f"{stem}.md").write_text(f"# {subject}\n\n{summary_md}\n", encoding="utf-8")
    (out / f"{stem}.json").write_text(json.dumps(
        {"as_of": result["as_of"], "groups": result["groups"], "signals": result["signals"],
         "overnight": result["overnight"]},
        ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log.info("Saved %s/%s.*", out, stem)

    # 5) email
    if args.no_email or args.demo:
        log.info("Email skipped")
    elif not (os.getenv("EMAIL_USER") and os.getenv("EMAIL_APP_PASSWORD")):
        log.warning("EMAIL_USER / EMAIL_APP_PASSWORD not set — report saved, email skipped")
    else:
        from src import emailer
        emailer.send(subject, html, text)
        log.info("Email sent: %s", subject)
        marker.write_text(subject + "\n", encoding="utf-8")


def pd_date(ts):
    import pandas as pd
    return pd.Timestamp(ts).date()


if __name__ == "__main__":
    main()
