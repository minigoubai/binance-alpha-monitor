#!/usr/bin/env python3
"""
Alpha 扫描复盘闭环 — alpha_review_loop.py
每次扫描（job a73bfe2490c4）后5分钟执行（job 8b02db475ac6）

核心逻辑：
  1. 选 24h 涨跌幅最大的代币（不论涨跌，各取Top6）
  2. 深度分析：DexScreener 市场数据 + surf token-holders（额度耗尽则用市场数据估算）
  3. 判断当前阶段：收集 / 拉升 / 出货 / 砸盘 / 整理
  4. 输出 alpha_review_loop_report.json 供发帖脚本使用

工具：Binance Alpha API + DexScreener + surf token-holders
"""

import requests, json, time, subprocess, os, re
from datetime import datetime
from collections import defaultdict

# ====== 文件路径 ======
SCAN_OUTPUT      = "/home/ubuntu/.hermes/scripts/alpha_scan_output.md"
REVIEW_OUTPUT    = "/home/ubuntu/.hermes/scripts/alpha_review_loop_report.json"
CHIP_SNAP_FILE   = "/home/ubuntu/.hermes/scripts/alpha_chip_snapshots.json"  # 筹码快照

# ====== 阈值 ======
TOP_MOVERS_COUNT = 3   # 各取Top3（涨+跌 = 最多6个）

# ====== 工具函数 ======
def load_json(path, default=None):
    if default is None: default = {}
    try:
        with open(path) as f: return json.load(f)
    except: return default

def save_json(path, data):
    with open(path, "w") as f: json.dump(data, f, ensure_ascii=False, indent=2)

# ====== 核心：获取代币当前市场数据（DexScreener）=======
def get_token_market_data(contract, chain_id="56"):
    """从 DexScreener 获取代币市场数据"""
    chain_num_to_str = {"56": "bsc", "1": "ethereum", "8453": "base", "42161": "arbitrum"}
    dex_chain = chain_num_to_str.get(str(chain_id), "bsc").lower()
    try:
        resp = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{contract}",
            timeout=10
        )
        pairs = resp.json().get("pairs", [])
        chain_pairs = [p for p in pairs if (p.get("chainId") or "").lower() == dex_chain]
        chain_pairs = chain_pairs or (pairs[:1] if pairs else [])
        if not chain_pairs:
            return {}
        p = chain_pairs[0]
        txns_h24 = p.get("txns", {}).get("h24", {})
        txns_h1  = p.get("txns", {}).get("h1", {})
        return {
            "price":           float(p.get("priceUsd", 0) or 0),
            "mcap":            float(p.get("marketCap", 0) or 0),
            "fdv":             float(p.get("fdv", 0) or 0),
            "liquidity":       float(p.get("liquidity", {}).get("usd", 0) or 0),
            "vol_24h":         float(p.get("volume", {}).get("h24", 0) or 0),
            "buys_24h":        int(txns_h24.get("buys", 0) or 0),
            "sells_24h":       int(txns_h24.get("sells", 0) or 0),
            "buys_h1":         int(txns_h1.get("buys", 0) or 0),
            "sells_h1":        int(txns_h1.get("sells", 0) or 0),
            "mcap_to_liq":     float(p.get("marketCap", 0) or 0) / max(float(p.get("liquidity", {}).get("usd", 0) or 1), 1),
            "fdv_to_mcap":     float(p.get("fdv", 0) or 0) / max(float(p.get("marketCap", 0) or 1), 1),
            "price_change_24h": float(p.get("priceChange", {}).get("h24", 0) or 0),
        }
    except:
        return {}

# ====== 核心：获取代币筹码分布（surf token-holders）=======
def get_token_holders(contract, chain_id="56"):
    """
    用 surf token-holders 分析代币筹码分布
    额度耗尽时返回 None，此时用市场数据估算阶段
    """
    chain_map = {"56": "bsc", "1": "ethereum", "8453": "base", "42161": "arbitrum"}
    ch = chain_map.get(str(chain_id), "bsc")
    try:
        result = subprocess.run(
            ["surf", "token-holders", "--chain", ch, "--address", contract, "--limit", "15"],
            capture_output=True, text=True, timeout=30
        )
        lines = result.stdout.strip().split("\n")
        raw = "\n".join(lines)
        try:
            holder_data = json.loads(raw)
        except:
            return None

        # 检查额度耗尽
        if isinstance(holder_data, dict) and holder_data.get("error", {}).get("code") == "PAID_BALANCE_ZERO":
            print(f"    [surf额度耗尽] {contract[:10]}")
            return None

        holders = holder_data.get("data", []) if isinstance(holder_data, dict) else holder_data
        if not holders:
            return None

        top5_ratio  = sum(float(h.get("percentage", 0)) for h in holders[:5])
        top10_ratio = sum(float(h.get("percentage", 0)) for h in holders[:10])
        cex_ratio   = 0.0
        dex_ratio   = 0.0
        cex_labels  = []

        for h in holders[:10]:
            pct = float(h.get("percentage", 0))
            et  = (h.get("entity_type") or "").lower()
            en  = (h.get("entity_name") or "").lower()
            if any(x in et + en for x in ["exchange", "cex", "binance", "coinbase", "okx", "kucoin", "mexc", "bybit", "bitget"]):
                cex_ratio += pct
                nm = h.get("entity_name", "CEX")
                if nm not in cex_labels:
                    cex_labels.append(nm)
            elif any(x in et for x in ["dex", "defi", "pancakeswap", "uniswap", "curve"]):
                dex_ratio += pct

        top1_pct    = float(holders[0].get("percentage", 0))
        top1_addr   = holders[0].get("address", "")
        top1_type   = holders[0].get("entity_type") or ""
        top1_name   = holders[0].get("entity_name") or ""
        top1_unknown = top1_pct > 15 and not top1_type

        return {
            "top5_ratio":   top5_ratio,
            "top10_ratio":  top10_ratio,
            "cex_ratio":    cex_ratio,
            "dex_ratio":    dex_ratio,
            "top1_pct":     top1_pct,
            "top1_addr":    top1_addr,
            "top1_label":   top1_name or "未知",
            "holder_count": len(holders),
            "top1_unknown": top1_unknown,
            "cex_labels":   cex_labels[:3],
            "holders":      holders[:5],
        }
    except:
        return None

# ====== 筹码快照：保存/读取 ======
def save_chip_snapshot(contract, chain_id, symbol, holders_data, market_data, tag=""):
    """保存代币筹码快照到本地"""
    snaps = load_json(CHIP_SNAP_FILE, {})
    key = f"{symbol}_{contract[:10]}"
    snaps[key] = {
        "symbol":    symbol,
        "contract":  contract,
        "chain_id":  chain_id,
        "tag":       tag,
        "saved_at":  datetime.now().isoformat(),
        "holders":   holders_data,
        "market":    market_data,
    }
    save_json(CHIP_SNAP_FILE, snaps)
    return snaps[key]

def get_chip_snapshot(contract, symbol):
    """读取某个代币的历史快照"""
    snaps = load_json(CHIP_SNAP_FILE, {})
    key = f"{symbol}_{contract[:10]}"
    return snaps.get(key)

# ====== 获取 Binance Alpha 涨跌幅榜 ======
def fetch_alpha_movers():
    """获取 Binance Alpha 涨跌幅榜（按24h变化率排序）"""
    try:
        r = requests.get(
            "https://www.binance.com/bapi/defi/v1/public/alpha-trade/aggTicker24?dataType=aggregate",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=10
        )
        data = r.json()
        tokens = data.get("data", [])
        sorted_tokens = sorted(tokens, key=lambda x: abs(float(x.get("percentChange24h", 0) or 0)), reverse=True)
        result = []
        for t in sorted_tokens[:30]:
            result.append({
                "symbol":     t.get("symbol", ""),
                "address":    t.get("contractAddress", ""),
                "chain_id":   str(t.get("chainId", "56")),
                "change_24h": float(t.get("percentChange24h", 0) or 0),
                "mcap":       float(t.get("marketCap", 0) or 0),
                "liquidity":  float(t.get("liquidity", 0) or 0),
                "score":      float(t.get("score", 0) or 0),
            })
        return result
    except:
        return []

# ====== 判断当前阶段（有筹码数据时）=======
def judge_stage_with_holders(change_24h, holders_data, prev_snapshot=None):
    """
    有 surf 筹码数据时的阶段判断
    """
    cex_ratio  = holders_data.get("cex_ratio", 0)
    top5_ratio = holders_data.get("top5_ratio", 0)
    top1_pct   = holders_data.get("top1_pct", 0)

    # 有前快照：对比CEX变化
    if prev_snapshot and prev_snapshot.get("holders"):
        prev_h   = prev_snapshot["holders"]
        prev_cex = prev_h.get("cex_ratio", 0)
        prev_top1= prev_h.get("top1_pct", 0)
        cex_delta = cex_ratio - prev_cex

        if cex_delta > 5 and top1_pct < prev_top1:
            return "出货"
        if cex_delta > 3:
            return "收集"

    # 无快照：靠当前结构
    if cex_ratio > 40 and top1_pct > 15:
        return "收集"
    if cex_ratio > 35 and top5_ratio < 55:
        return "出货"

    return None  # 交给市场数据判断

# ====== 判断当前阶段（纯市场数据估算）=======
def judge_stage_by_market(change_24h, market_data):
    """
    无 surf 筹码数据时，用市场数据估算阶段
    - 涨幅 > 15% + 买盘主导 → 拉升
    - 跌幅 > 15% + 卖盘主导 → 砸盘
    - 涨幅 5-15% + 买卖均衡 → 整理
    - 跌幅 5-15% + 买卖均衡 → 整理
    - 跌幅 < -5% + 卖盘极主导 → 砸盘
    - 涨幅 < 5% + 买卖比稳定 → 整理
    """
    buys_h1   = market_data.get("buys_h1", 0)
    sells_h1  = market_data.get("sells_h1", 0)
    buys_24h  = market_data.get("buys_24h", 0)
    sells_24h = market_data.get("sells_24h", 0)
    bs_h1     = buys_h1 / max(sells_h1, 1)
    bs_24h    = buys_24h / max(sells_24h, 1)

    chg = abs(change_24h)

    if change_24h > 15:
        if bs_h1 > 1.3 or bs_24h > 1.3:
            return "拉升"
        return "拉升"  # 大涨默认拉升

    if change_24h < -15:
        if sells_h1 > buys_h1 * 1.5 or sells_24h > buys_24h * 1.5:
            return "砸盘"
        return "砸盘"  # 大跌默认砸盘

    if change_24h > 5:
        return "整理偏强"

    if change_24h < -5:
        return "整理偏弱"

    return "整理"

# ====== 深度筹码分析单币 ======
def deep_chip_analysis(contract, chain_id, symbol, tag=""):
    """
    对单个代币做深度分析
    1. 获取当前市场数据（DexScreener）
    2. 获取当前筹码分布（surf token-holders，失败则None）
    3. 对比历史快照（若有）
    4. 判断当前阶段
    """
    mkt = get_token_market_data(contract, chain_id)
    hld = get_token_holders(contract, chain_id)

    if not mkt and not hld:
        return None

    change_24h = mkt.get("price_change_24h", 0)
    stage = "数据不足"

    result = {
        "symbol":      symbol,
        "contract":    contract,
        "chain_id":    chain_id,
        "tag":         tag,
        "analyzed_at": datetime.now().isoformat(),
        "market":      mkt,
        "holders":     hld,
        "holders_available": hld is not None,
        "snapshot_prev": None,
        "chip_changes": [],
        "risk_flags":  [],
        "stage":       "数据不足",
        "change_24h":  change_24h,
    }

    # 对比历史快照（仅当有筹码数据时）
    prev = get_chip_snapshot(contract, symbol)
    if prev and prev.get("holders") and hld:
        result["snapshot_prev"] = prev
        old = prev["holders"]
        new = hld

        def chg_str(old_val, new_val):
            if not old_val or not new_val:
                return "数据不足"
            d = new_val - old_val
            if abs(d) < 0.5:
                return "基本持平"
            return f"{'+' if d > 0 else ''}{d:.1f}%"

        changes = []
        changes.append(f"Top5集中度: {old.get('top5_ratio',0):.1f}% -> {new.get('top5_ratio',0):.1f}% ({chg_str(old.get('top5_ratio'), new.get('top5_ratio'))})")
        changes.append(f"CEX占比: {old.get('cex_ratio',0):.1f}% -> {new.get('cex_ratio',0):.1f}% ({chg_str(old.get('cex_ratio'), new.get('cex_ratio'))})")
        changes.append(f"DEX占比: {old.get('dex_ratio',0):.1f}% -> {new.get('dex_ratio',0):.1f}% ({chg_str(old.get('dex_ratio'), new.get('dex_ratio'))})")
        changes.append(f"Top1单地址: {old.get('top1_pct',0):.1f}% -> {new.get('top1_pct',0):.1f}% ({chg_str(old.get('top1_pct'), new.get('top1_pct'))})")
        result["chip_changes"] = changes

        stage_hint = judge_stage_with_holders(change_24h, hld, prev)
        if stage_hint:
            stage = stage_hint

    # 保存当前快照
    save_chip_snapshot(contract, chain_id, symbol, hld, mkt, tag=tag)

    # 阶段判断（若筹码数据无法判断）
    if stage == "数据不足" and mkt:
        stage = judge_stage_by_market(change_24h, mkt)

    result["stage"] = stage

    # 风险标记
    if mkt:
        if mkt.get("mcap_to_liq", 0) > 500:
            result["risk_flags"].append("流动性极低")
        if mkt.get("fdv_to_mcap", 0) > 3:
            result["risk_flags"].append("FDV膨胀")
        if mkt.get("mcap", 0) < 10_000:
            result["risk_flags"].append("市值极低")

    if hld:
        if hld.get("top5_ratio", 0) > 70:
            result["risk_flags"].append(f"Top5高度控盘{hld['top5_ratio']:.0f}%")
        if hld.get("top1_unknown", False) and hld.get("top1_pct", 0) > 20:
            result["risk_flags"].append("Top1无标签可能团队锁仓")

    return result

# ====== 主流程 ======
def main():
    now = datetime.now()
    print(f"[复盘] 开始 {now.strftime('%Y-%m-%d %H:%M')}")

    # 1. 获取 Alpha 涨跌幅榜
    print("[1/5] 获取Alpha涨跌幅榜...")
    movers = fetch_alpha_movers()
    gainers = sorted([m for m in movers if m["change_24h"] > 0], key=lambda x: x["change_24h"], reverse=True)
    losers  = sorted([m for m in movers if m["change_24h"] < 0], key=lambda x: x["change_24h"])
    print(f"    涨榜 {len(gainers)} / 跌榜 {len(losers)}")

    # 2. 选Top6涨 + Top6跌
    top_movers = (gainers[:TOP_MOVERS_COUNT] if gainers else []) + \
                  (losers[:TOP_MOVERS_COUNT] if losers else [])
    print(f"    深度分析: {len(top_movers)} 个代币")

    # 3. 深度分析
    print("[2/5] 深度筹码分析...")
    analyzed = []
    for m in top_movers:
        sym      = m["symbol"].upper()
        addr     = m["address"]
        chain_id = m["chain_id"]
        if not addr:
            continue
        print(f"    分析: {sym} {m['change_24h']:+.1f}% ...", end="", flush=True)
        analysis = deep_chip_analysis(addr, chain_id, sym, tag=f"24h {m['change_24h']:+.1f}%")
        if analysis:
            hld_ok = analysis.get("holders_available", False)
            print(f" -> {analysis['stage']} | 筹码:{'有' if hld_ok else '无'}")
            analyzed.append(analysis)
        else:
            print(" -> 失败")
        time.sleep(1)

    # 4. 按涨幅排序
    analyzed.sort(key=lambda x: x.get("change_24h", 0), reverse=True)

    # 5. 生成报告
    print("[3/5] 生成复盘报告...")

    stage_summary = {
        "surge":   sum(1 for a in analyzed if a.get("stage") == "拉升"),
        "accum":   sum(1 for a in analyzed if a.get("stage") == "收集"),
        "dist":    sum(1 for a in analyzed if a.get("stage") == "出货"),
        "selloff": sum(1 for a in analyzed if a.get("stage") == "砸盘"),
        "neutral": sum(1 for a in analyzed if a.get("stage") in ["整理","整理偏强","整理偏弱","数据不足"]),
    }

    # 建议
    rec = ""
    if stage_summary["surge"]:
        rec = f"检测到 {stage_summary['surge']} 个代币处于拉升阶段"
    elif stage_summary["selloff"]:
        rec = f"检测到 {stage_summary['selloff']} 个代币处于砸盘阶段"
    elif stage_summary["accum"]:
        rec = f"检测到 {stage_summary['accum']} 个代币处于筹码收集阶段"
    elif stage_summary["dist"]:
        rec = f"检测到 {stage_summary['dist']} 个代币处于出货阶段"

    report = {
        "reviewed_at":  now.isoformat(),
        "top_movers":   analyzed,
        "summary":      stage_summary,
        "recommendation": rec,
        "note":         "surf token-holders 额度耗尽时，阶段判断基于市场数据（买卖比/成交量）",
    }

    save_json(REVIEW_OUTPUT, report)
    print(f"\n[完成] 复盘报告 -> {REVIEW_OUTPUT}")
    print(f"  拉升: {stage_summary['surge']} / 收集: {stage_summary['accum']} / 出货: {stage_summary['dist']} / 砸盘: {stage_summary['selloff']} / 整理: {stage_summary['neutral']}")

    return report

if __name__ == "__main__":
    main()
