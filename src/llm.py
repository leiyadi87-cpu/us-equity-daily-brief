"""AI layer: turn the quant table + headlines into a written brief."""
from __future__ import annotations

import logging
import math
import os

from .analysis import top_movers

log = logging.getLogger(__name__)

SYSTEM = {
    "zh": """你是一名买方股票研究分析师，为一位量化金融背景的投资者撰写每日美股盘前简报。

规则：
- 只使用下面提供的数据和新闻。不要编造数字、事件或原因。
- 某个异动如果找不到对应新闻，就写“暂无明确新闻驱动”。
- 区分事实和推断；推断用“可能”“或因”等措辞。
- 不给出买卖建议。

用 Markdown 输出，结构如下：
## 一句话总结
## 大盘与情绪
（SPY / QQQ / VIX，2–3 句）
## 板块轮动
（哪些板块领涨、领跌，和 1 个月趋势对比；包括贵金属与大宗商品）
## 个股异动与驱动
（3–6 条，每条格式：**代码** 涨跌幅 — 原因，并说明对应哪条新闻）
## 技术信号
（挑最重要的 2–4 个：超买超卖、均线突破、放量等）
## 今日关注
（2–4 条，只基于新闻中出现的事件：财报、宏观数据、政策、风险）

全文控制在 600 字以内。""",
    "en": """You are a buy-side equity research analyst writing a pre-market US equities brief for a quantitatively trained investor.

Rules:
- Use only the data and headlines provided. Never invent numbers, events or causes.
- If a move has no matching headline, say "no clear news catalyst".
- Separate facts from inference; hedge inferences ("likely", "possibly").
- No buy/sell recommendations.

Output Markdown with these sections:
## One-line summary
## Market & sentiment  (SPY / QQQ / VIX, 2-3 sentences)
## Sector rotation  (leaders/laggards vs 1-month trend, incl. metals & commodities)
## Notable movers & catalysts  (3-6 bullets: **TICKER** move — reason, citing the headline)
## Technical signals  (the 2-4 most important)
## Watch today  (2-4 items, only from events mentioned in the headlines)

Keep it under 350 words.""",
}


def _f(x, pct=True):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:+.1%}" if pct else f"{x:.2f}"


def build_payload(result: dict, news: list[dict]) -> str:
    lines = [f"Data as of close: {result['as_of']}", ""]
    for g in result["groups"]:
        head = f"### {g['name']}"
        if g["etf"]:
            e = g["etf"]
            head += f"  (benchmark {e['ticker']}: 1D {_f(e['ret_1d'])}, 1M {_f(e['ret_1m'])}, YTD {_f(e['ret_ytd'])})"
        lines += [head, "ticker | close | 1D | 5D | 1M | YTD | RSI14 | vs200DMA | vol/avg20"]
        for r in g["rows"]:
            lines.append(" | ".join([
                r["name"] if r["name"] == r["ticker"] else f"{r['ticker']} ({r['name']})",
                f"{r['close']:.2f}", _f(r["ret_1d"]), _f(r["ret_5d"]), _f(r["ret_1m"]), _f(r["ret_ytd"]),
                f"{r['rsi14']:.0f}", _f(r["vs_sma200"]),
                "n/a" if math.isnan(r["vol_ratio"]) else f"{r['vol_ratio']:.1f}x"]))
        lines.append("")
    lines.append("### Quant signals (ranked)")
    lines += [f"- {s['text']}" for s in result["signals"][:15]] or ["- none"]
    lines += ["", "### Headlines (last 36h)"]
    for n in news:
        ts = n["time"].strftime("%m-%d %H:%M UTC") if n.get("time") else ""
        lines.append(f"- [{n['ticker']}] {n['title']} — {n.get('publisher', '')} {ts}")
    if not news:
        lines.append("- (no headlines retrieved)")
    return "\n".join(lines)


def summarize(result: dict, news: list[dict], cfg: dict) -> tuple[str, str]:
    """Returns (markdown, source) where source is the model name or 'rule-based'."""
    lang = cfg.get("language", "zh")
    llm = cfg.get("llm", {})
    provider, model = llm.get("provider", "none"), llm.get("model")
    payload = build_payload(result, news)
    try:
        if provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
            import anthropic
            msg = anthropic.Anthropic().messages.create(
                model=model, max_tokens=2000, system=SYSTEM[lang],
                messages=[{"role": "user", "content": payload}])
            return "".join(b.text for b in msg.content if b.type == "text"), model
        if provider == "openai" and os.getenv("OPENAI_API_KEY"):
            from openai import OpenAI
            r = OpenAI().chat.completions.create(
                model=model, messages=[{"role": "system", "content": SYSTEM[lang]},
                                       {"role": "user", "content": payload}])
            return r.choices[0].message.content, model
    except Exception as e:
        log.error("LLM call failed, falling back to rule-based summary: %s", e)
    return rule_based(result, lang), "rule-based"


def rule_based(result: dict, lang: str = "zh") -> str:
    """No-API fallback so the pipeline always produces something."""
    by = {r["ticker"]: r for g in result["groups"] for r in g["rows"]}
    sectors = [(g["name"], (g["etf"] or {}).get("ret_1d", g["avg"]["ret_1d"])) for g in result["groups"][1:]]
    sectors = [s for s in sectors if not math.isnan(s[1])]
    sectors.sort(key=lambda x: x[1], reverse=True)
    up, down = top_movers(result, 3)
    zh = lang == "zh"
    out = ["## " + ("市场概览" if zh else "Market overview")]
    mk = [f"{t} {_f(by[t]['ret_1d'])}" for t in ("SPY", "QQQ") if t in by]
    if "^VIX" in by:
        mk.append(f"VIX {by['^VIX']['close']:.1f} ({_f(by['^VIX']['ret_1d'])})")
    out.append("- " + ", ".join(mk))
    if sectors:
        out.append(("- 领涨板块：" if zh else "- Leading: ") + f"{sectors[0][0]} {_f(sectors[0][1])}")
        out.append(("- 领跌板块：" if zh else "- Lagging: ") + f"{sectors[-1][0]} {_f(sectors[-1][1])}")
    out.append(("- 涨幅前列：" if zh else "- Top gainers: ") + ", ".join(f"{r['name']} {_f(r['ret_1d'])}" for r in up))
    out.append(("- 跌幅前列：" if zh else "- Top losers: ") + ", ".join(f"{r['name']} {_f(r['ret_1d'])}" for r in down))
    out.append("\n## " + ("技术信号" if zh else "Signals"))
    out += [f"- {s['text']}" for s in result["signals"][:8]] or ["- —"]
    out.append("\n*" + ("未配置 AI API key，以上为规则生成的摘要。" if zh else "No AI key configured — rule-based summary.") + "*")
    return "\n".join(out)
