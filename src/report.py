"""Render the HTML email (and a plain-text fallback)."""
from __future__ import annotations

import math
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader

TEXT = {
    "zh": {"kicker": "Daily US Market Brief", "title": "每日美股研究简报", "as_of": "数据截至收盘",
           "written_by": "简报撰写：", "sectors": "板块记分卡", "sector": "板块", "ticker": "代码",
           "close": "收盘", "vol": "量比", "news": "相关新闻",
           "footer": "自动生成：Python 计算指标 · AI 撰写摘要 · GitHub Actions 定时发送。数据来自 Yahoo Finance，仅供研究，不构成投资建议。",
           "missing": "以下代码未取到数据：", "etf": "ETF", "eq": "成分股均值"},
    "en": {"kicker": "Daily US Market Brief", "title": "Daily US Market Brief", "as_of": "Data as of close",
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
    spy = by.get("SPY")
    subject = f"{t['title']} {result['as_of']}" + (f" | SPY {pct(spy['ret_1d'])}" if spy else "")

    env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "templates"),
                      autoescape=True)
    html = env.get_template("brief.html.j2").render(
        title=t["title"], t=t, as_of=result["as_of"], demo=demo, snapshot=snapshot,
        summary_html=_md_to_email_html(summary_md), source=source, sectors=sectors,
        groups=result["groups"], news=news, missing=result["missing"],
        pct=pct, color=color, heat=heat, volx=volx)
    return subject, html, summary_md


def _md_to_email_html(md: str):
    from markupsafe import Markup
    html = markdown.markdown(md, extensions=["extra", "sane_lists"])
    # email clients ignore <style>, so inline a few styles
    html = (html.replace("<h2>", '<h2 style="font-size:16px;margin:18px 0 6px;color:#0b1f3a;border-left:3px solid #0b1f3a;padding-left:8px;">')
                .replace("<ul>", '<ul style="margin:4px 0;padding-left:20px;">')
                .replace("<p>", '<p style="margin:4px 0;">'))
    return Markup(html)
