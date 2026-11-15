"""Render the HTML email (and a plain-text fallback)."""
from __future__ import annotations

import math
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader

TEXT = {
    "zh": {"kicker": "Daily US Market Brief", "title": "每日美股盘前简报", "as_of": "美股数据",
           "as_of_tail": "正式收盘", "gen": "隔夜数据截至", "overnight": "隔夜全景 · 亚洲 / 欧洲 / 期货 / 利率汇率",
           "market": "市场", "last": "最新", "chg": "涨跌", "status": "状态",
           "ext": "美股盘后 / 盘前异动（相对前一交易日正式收盘）", "ah": "盘后 16–20 ET", "latest": "最新（时间 ET）",
           "phase": {"post": "盘后", "pre": "盘前", "regular": "盘中"},
           "intraday_warn": "提示：本简报实际生成于美股交易时段（{time} ET，GitHub 定时任务有延迟）。美股表格仍是 {session} 的正式收盘数据；“最新”列为盘中价格，不是收盘价。",
           "written_by": "简报撰写：", "sectors": "板块记分卡", "sector": "板块", "ticker": "代码",
           "close": "收盘", "vol": "量比", "news": "相关新闻",
           "footer": "自动生成：Python 计算指标 · AI 撰写摘要 · GitHub Actions 定时发送。数据来自 Yahoo Finance，仅供研究，不构成投资建议。",
           "missing": "以下代码未取到数据：", "etf": "ETF", "eq": "成分股均值"},
    "en": {"kicker": "Daily US Market Brief", "title": "Daily US Pre-market Brief", "as_of": "US data:",
           "as_of_tail": "official close", "gen": "Overnight data as of", "overnight": "Overnight · Asia / Europe / futures / rates & FX",
           "market": "Market", "last": "Last", "chg": "Chg", "status": "Status",
           "ext": "US after-hours / pre-market movers (vs official close)", "ah": "After-hours", "latest": "Latest (ET)",
           "phase": {"post": "after-hours", "pre": "pre-market", "regular": "intraday"},
           "intraday_warn": "Note: this brief was generated during US regular hours ({time} ET, GitHub schedule delay). US tables are still the official close of {session}; the Latest column is intraday.",
           "written_by": "Written by:", "sectors": "Sector scoreboard", "sector": "Sector", "ticker": "Ticker",
           "close": "Close", "vol": "Vol/avg", "news": "Headlines",
           "footer": "Auto-generated: Python indicators · AI summary · GitHub Actions schedule. Data from Yahoo Finance. Research only, not investment advice.",
           "missing": "No data for:", "etf": "ETF", "eq": "equal-wt"},
}

GREEN, RED, GREY = "#15803d", "#dc2626", "#6b7280"


def _nan(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


def render(result: dict, news: list[dict], summary_md: str, source: str, cfg: dict, demo: bool = False) -> tuple[str, str, str]:
    """Returns (subject, html, plain_text)."""
    lang = cfg.get("language", "zh")
    t = TEXT[lang]
    up, down = (GREEN, RED) if cfg.get("up_color", "green") == "green" else (RED, GREEN)

    def pct(x):
        return "—" if _nan(x) else f"{x:+.1%}"

    def color(x, ticker=None):
        if _nan(x) or x == 0:
            return GREY
        good = x > 0
        if ticker == "^VIX":  # rising VIX is bad news
            good = not good
        return up if good else down

    def heat(x, k="ret_1d"):
        if _nan(x):
            return "transparent"
        scale = {"ret_1d": 0.02, "ret_5d": 0.04, "ret_1m": 0.08, "ret_ytd": 0.25}[k]
        a = min(abs(x) / scale, 1) * 0.18
        rgb = "21,128,61" if (x > 0) == (up == GREEN) else "220,38,38"
        return f"rgba({rgb},{a:.2f})"

    def volx(x):
        return "—" if _nan(x) else f"{x:.1f}x"

    sectors = []
    for g in result["groups"][1:]:
        if g["etf"]:
            sectors.append({"name": g["name"], "basis": g["etf"]["ticker"], **{k: g["etf"][k] for k in g["avg"]}})
        else:
            sectors.append({"name": g["name"], "basis": t["eq"], **g["avg"]})

    snapshot = result["groups"][0]["rows"] if result["groups"] else []
    by = {r["ticker"]: r for g in result["groups"] for r in g["rows"]}
    for g in result["groups"]:
        if g.get("etf"):
            by.setdefault(g["etf"]["ticker"], {**g["etf"], "name": g["etf"]["ticker"]})
    spy = by.get("SPY")

    o = result.get("overnight") or {}
    glob = o.get("global", [])
    es = next((r for r in glob if r["ticker"] == "ES=F"), None)
    gen = o.get("generated_et", "")
    subject = (f"{t['title']} {gen[:10] or result['as_of']}"
               + (f" | {result['as_of'][5:]} SPY {pct(spy['ret_1d'])}" if spy else "")
               + (f" | ES期货 {pct(es['pct'])}" if es and lang == "zh" else (f" | ES {pct(es['pct'])}" if es else "")))

    def chg(r):
        if "chg_bp" in r and not _nan(r.get("chg_bp")):
            return f"{r['chg_bp']:+.1f} bp"
        return pct(r.get("pct"))

    def chg_color(r):
        if "chg_bp" in r and not _nan(r.get("chg_bp")):
            return GREY
        return color(r.get("pct"))

    ext = o.get("extended", {})
    thr = result.get("ext_move_min", 0.015)
    pick = [x for x in ("SPY", "QQQ") if x in ext]
    pick += sorted([x for x, e in ext.items() if x not in pick and
                    (abs(e["latest_pct"]) >= thr or (not _nan(e["ah_pct"]) and abs(e["ah_pct"]) >= thr))],
                   key=lambda x: -abs(ext[x]["latest_pct"]))[:12]
    ext_rows = [{"ticker": x, "name": by.get(x, {}).get("name", x), "close": by.get(x, {}).get("close"), **ext[x]}
                for x in pick]
    warn = (t["intraday_warn"].format(time=gen[11:], session=result["as_of"])
            if o.get("phase") == "regular" else "")

    env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "templates"),
                      autoescape=True)
    html = env.get_template("brief.html.j2").render(
        title=t["title"], t=t, as_of=result["as_of"], demo=demo, snapshot=snapshot,
        summary_html=_md_to_email_html(summary_md), source=source, sectors=sectors,
        groups=result["groups"], news=news, missing=result["missing"],
        pct=pct, color=color, heat=heat, volx=volx,
        gen=gen, glob=glob, chg=chg, chg_color=chg_color, ext_rows=ext_rows, warn=warn)
    return subject, html, summary_md


def _md_to_email_html(md: str):
    from markupsafe import Markup
    html = markdown.markdown(md, extensions=["extra", "sane_lists"])
    # email clients ignore <style>, so inline a few styles
    html = (html.replace("<h2>", '<h2 style="font-size:16px;margin:18px 0 6px;color:#0b1f3a;border-left:3px solid #0b1f3a;padding-left:8px;">')
                .replace("<ul>", '<ul style="margin:4px 0;padding-left:20px;">')
                .replace("<p>", '<p style="margin:4px 0;">'))
    return Markup(html)
