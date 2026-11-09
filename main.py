"""Daily US Market Brief — entry point.

    python main.py                 # full run: fetch → analyze → AI summary → email → save
    python main.py --no-email      # build and save the report only
    python main.py --demo          # offline run on synthetic data (no network, no keys)
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import yaml

from src import analysis, data, llm, report

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("brief")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=ROOT / "config.yaml")
    ap.add_argument("--no-email", action="store_true", help="skip sending email")
    ap.add_argument("--no-ai", action="store_true", help="use the rule-based summary")
    ap.add_argument("--demo", action="store_true", help="synthetic data, offline")
    ap.add_argument("--out", default=ROOT / "reports")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

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

    # 2) quant
    result = analysis.analyze(prices, cfg)

    # 3) AI
    summary_md, source = llm.summarize(result, news, cfg)
    log.info("Summary written by %s", source)

    # 4) render + save
    subject, html, text = report.render(result, news, summary_md, source, cfg, demo=args.demo)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = ("demo-" if args.demo else "") + result["as_of"]
    (out / f"{stem}.html").write_text(html, encoding="utf-8")
    (out / f"{stem}.md").write_text(f"# {subject}\n\n{summary_md}\n", encoding="utf-8")
    (out / f"{stem}.json").write_text(json.dumps(
        {"as_of": result["as_of"], "groups": result["groups"], "signals": result["signals"]},
        ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log.info("Saved %s/%s.*", out, stem)

    # 5) email
    if args.no_email or args.demo:
        log.info("Email skipped")
    else:
        from src import emailer
        emailer.send(subject, html, text)
        log.info("Email sent: %s", subject)


if __name__ == "__main__":
    main()
