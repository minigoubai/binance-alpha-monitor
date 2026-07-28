#!/usr/bin/env python3
"""
Alpha Square Post v2 — 暴涨暴跌代币筹码分析发帖
内容核心：
  - 选 24h 涨跌幅最大的代币（不论涨跌，各取Top5）
  - 深度筹码分析：CEX占比、Top5集中度、筹码变化
  - 判断当前阶段：收集 / 拉升 / 出货 / 砸盘
  - NO 合约地址，NO 漏扫原因
  - 工具：Binance Alpha + DexScreener + surf token-holders
"""

import subprocess, json, re, os, time
from datetime import datetime

SQUARE_API_KEY  = "eacb051bd0444620b92ab2a5db7c0d78"
SQUARE_API_URL  = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add"
REVIEW_LOOP     = "/home/ubuntu/.hermes/scripts/alpha_review_loop_report.json"
OUTPUT_MARKER   = "/home/ubuntu/.hermes/scripts/alpha_square_last_post.json"

def load_json(path, default=None):
    if default is None: default = {}
    try:
        with open(path) as f: return json.load(f)
    except: return default

def post_to_square(title, content):
    """发帖到币安广场 — bodyTextOnly 纯文本模式"""
    headers = {
        "X-Square-OpenAPI-Key": SQUARE_API_KEY,
        "Content-Type": "application/json",
        "clienttype": "binanceSkill"
    }
    payload = {"bodyTextOnly": content}
    try:
        import requests
        r = requests.post(SQUARE_API_URL, headers=headers, json=payload, timeout=15)
        resp = r.json()
        if resp.get("code") == "000000" or resp.get("success"):
            data = resp.get("data", {})
            article_id = data.get("id", "N/A")
            post_url = f"https://www.binance.com/square/post/{article_id}" if article_id != "N/A" else "N/A"
            with open(OUTPUT_MARKER, "w") as f:
                json.dump({
                    "posted_at": datetime.now().isoformat(),
                    "title": title,
                    "post_id": article_id,
                    "post_url": post_url,
                    "article_len": len(content)
                }, f, ensure_ascii=False, indent=2)
            print(f"[Square] Posted: {title}")
            print(f"[Square] URL: {post_url}")
            return True
        else:
            print(f"[Square] Failed: {resp}")
    except Exception as e:
        print(f"[Square] Error: {e}")
    return False

def fmt_usd(val):
    """格式化 USD 金额"""
    if val >= 1e9:   return f"${val/1e9:.2f}B"
    elif val >= 1e6: return f"${val/1e6:.2f}M"
    elif val >= 1e3: return f"${val/1e3:.1f}K"
    return f"${val:.2f}"

def fmt_chain(chain_id):
    """链ID -> 链名"""
    return {"56": "BSC", "1": "ETH", "8453": "Base", "42161": "Arb"}.get(str(chain_id), "BSC")

# ====== 核心发帖内容生成 ======
def gen_market_analysis_content(top_movers, summary):
    """
    生成市场异动筹码分析内容
    格式：纯文本，无emoji，无合约地址，极度精简
    """
    now = datetime.now().strftime("%m/%d %H:%M")
    lines = []

    # 标题
    lines.append(f"Alpha 暴涨暴跌筹码分析 | {now}")
    lines.append("工具：Binance Alpha + DexScreener")
    lines.append("=" * 40)

    # 阶段统计（一行）
    stage_count = summary or {}
    parts = []
    for stage, key in [("拉升", "surge"), ("收集", "accum"), ("出货", "dist"), ("砸盘", "selloff")]:
        cnt = stage_count.get(f"{key}_count", 0)
        if cnt:
            parts.append(f"{stage}{cnt}个")
    if parts:
        lines.append("阶段:" + " | ".join(parts))
        lines.append("")

    # 各币种分析（紧凑）
    for r in top_movers:
        sym       = r["symbol"]
        chain_id  = r.get("chain_id", "56")
        chain     = fmt_chain(chain_id)
        change    = r.get("change_24h", 0)
        stage     = r.get("stage", "?")
        mkt       = r.get("market", {})
        hld       = r.get("holders", {})
        risk_flags= r.get("risk_flags", [])

        # 代币行
        sign = "+" if change > 0 else "-"
        risk_str = " [风险]" if risk_flags else ""
        lines.append(f"{sym}({chain}) {sign}{abs(change):.1f}% | {stage}{risk_str}")

        # 筹码（有数据时一行）
        if hld:
            top5 = hld.get('top5_ratio', 0)
            cex  = hld.get('cex_ratio', 0)
            dex  = hld.get('dex_ratio', 0)
            top1 = hld.get('top1_pct', 0)
            top1_lbl = hld.get('top1_label', '')[:10]
            lines.append(f"  筹码: Top5{top5:.0f}% CEX{cex:.0f}% DEX{dex:.0f}% Top1{top1:.0f}%({top1_lbl})")
        elif r.get("holders_available") == False:
            lines.append(f"  筹码: 数据不可用（工具额度耗尽）")

        # 市场数据一行
        if mkt:
            mcap  = fmt_usd(mkt.get('mcap', 0))
            liq   = fmt_usd(mkt.get('liquidity', 0))
            ml    = mkt.get('mcap_to_liq', 0)
            buys  = mkt.get('buys_24h', 0)
            sells = mkt.get('sells_24h', 0)
            lines.append(f"  市值{mcap} 流动性{liq} 倍数{ml:.0f}x 买卖{buys:,}/{sells:,}")

        lines.append("")

    lines.append("Hermes Alpha Radar")
    return "\n".join(lines)

# ====== 无数据静默 ======
def skip_post(reason=""):
    """无信号时静默跳过，不发帖"""
    now = datetime.now().strftime("%m/%d %H:%M")
    print(f"[SquarePostv2] SKIP | 无信号跳过发帖 | {now} | {reason}")
    # 写入空白标记，区别于未运行
    with open(OUTPUT_MARKER, "w") as f:
        json.dump({
            "skipped_at": datetime.now().isoformat(),
            "reason": reason,
            "posted": False
        }, f, ensure_ascii=False, indent=2)

# ====== Main ======
def main():
    review = load_json(REVIEW_LOOP, {})

    signals    = review.get("signals", [])
    top_movers = review.get("top_movers", [])
    summary    = review.get("summary", {})

    now_str = datetime.now().strftime("%m/%d %H:%M")

    # 有信号 → 发信号追踪帖
    if signals:
        title   = f"Alpha 信号追踪 | {now_str}"
        content = gen_market_analysis_content(signals, summary)
        print(f"[SquarePostv2] Mode: SIGNAL_TRACKING | {title}")
        post_to_square(title, content)
    # 无信号 → 静默跳过，不发广场
    else:
        skip_post(reason=f"signals为空（无{chr(177)}30%信号），静默跳过")

if __name__ == "__main__":
    main()
