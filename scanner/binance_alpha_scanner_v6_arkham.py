#!/usr/bin/env python3
"""
Binance Alpha Scanner v6 — Hermes Radar + Arkham/Surf 筹码增强版

新增三层数据：
  1. Surf wallet-transfers  → 代币的链上资金流（谁在买/卖）
  2. Surf wallet-labels-batch → 交易对手 Arkham 标签（交易所/VC/鲸鱼）
  3. 新评分维度「筹码动向」   → 直接影响 ACCUMULATION / DISTRIBUTION

用法:
  python3 binance_alpha_scanner_v6_arkham.py
"""

import requests
import json
import time
import subprocess
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import os

# ====== 配置 ======
STATE_FILE = "/home/ubuntu/.hermes/scripts/alpha_scanner_state.json"
OUTPUT_FILE = "/home/ubuntu/.hermes/scripts/alpha_scan_output.md"
STATE_FILE_SCORES = "/home/ubuntu/.hermes/scripts/alpha_scanner_scores.json"
HOLDER_STATE_FILE = "/home/ubuntu/.hermes/scripts/alpha_holder_state.json"
REPLAY_FILE = "/home/ubuntu/.hermes/scripts/alpha_signal_replay.json"
SMART_MONEY_STATE = "/home/ubuntu/.hermes/scripts/alpha_smart_money_state.json"

MIN_SCORE = 100
MIN_LIQUIDITY = 50_000
MIN_HOLDERS = 500

GAIN_ACCUMULATION_EARLY = 10
GAIN_OVERHEATED = 30
DUMP_THRESHOLD = -30

# v6 新增：Arkham/Surf 权重分配
# v7 融合：HertzFlow 筹码三分法 + Dumper 分类
SCORE_WEIGHTS = {
    "资金强度": 20,      # 原有：Binance Alpha Score
    "地址质量": 15,      # 原有：持币地址数
    "市场确认": 15,      # 原有：成交量异动 + RSI + 溢价
    "催化权重": 10,      # 原有：解锁 + 持币趋势
    "流动性": 8,         # 原有
    "时间优势": 7,       # 原有
    "筹码动向": 10,      # v6：Smart Money 流向（降低权重让给 HertzFlow）
    "风险结构": 10,       # 原有（权重提高）
    "筹码三分法": 10,     # v7 新增：Operator/CEX Pool/Verifiable Retail
    "Dumper分类": 5,      # v7 新增：Quiet/Partial/Full Dumper
}

# HertzFlow v7 常量
HFTZ_WEIGHTS = {
    "资金强度": 20,
    "地址质量": 15,
    "市场确认": 15,
    "催化权重": 10,
    "流动性": 8,
    "时间优势": 7,
    "筹码动向": 10,
    "风险结构": 10,
    "筹码三分法": 10,
    "Dumper分类": 5,
}

# 已知交易所/做市商 entity_type
EXCHANGE_TYPES = {"cex", "exchange"}
# 已知 VC / 机构 entity_type
VC_TYPES = {"fund", "vc", "venture"}
# 鲸鱼 entity_type
WHALE_TYPES = {"whale", "pool", "protocol"}
# DEX entity_type
DEX_TYPES = {"dex", "decentralized_exchange"}

# ====== Surf 封装 ======

def surf_cmd(args_str, timeout=20):
    """执行 surf CLI 命令并返回 JSON，超时返回 None"""
    try:
        result = subprocess.run(
            f"surf {args_str}",
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return None
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def get_market_fear_greed():
    """surf market-fear-greed → 市场情绪（1次调用）"""
    data = surf_cmd("market-fear-greed --limit=1", timeout=15)
    if data and isinstance(data, dict):
        items = data.get("data", [])
        if items:
            item = items[0]
            value = item.get("value", 0)
            return {
                "value": value,
                "classification": item.get("classification", "Neutral"),
                "signal": "Fear" if value < 40 else ("Greed" if value > 60 else "Neutral")
            }
    return {"value": 50, "classification": "Neutral", "signal": "Neutral"}


def get_market_top_movers():
    """surf market-ranking → 快速获取异动榜（2次调用）"""
    movers = {"gainers": [], "losers": []}
    # 用 --sort-by=change_24h 替代 --metric=top_gainers（--metric 是错误参数）
    gainers = surf_cmd("market-ranking --sort-by=change_24h --order=desc --limit=20", timeout=30)
    if gainers and isinstance(gainers, dict):
        movers["gainers"] = gainers.get("data", [])[:10]
    losers = surf_cmd("market-ranking --sort-by=change_24h --order=asc --limit=20", timeout=30)
    if losers and isinstance(losers, dict):
        movers["losers"] = losers.get("data", [])[:10]
    return movers


def get_token_tech_indicators(symbol):
    """surf market-price-indicator → RSI（只对 B+ 候选，1次调用）"""
    data = surf_cmd(f'market-price-indicator --exchange=binance --symbol={symbol}/USDT --indicator=rsi --interval=1d', timeout=15)
    if data and isinstance(data, dict):
        items = data.get("data", [])
        if items:
            val = items[0].get("value")
            return {"rsi": val}
    return {"rsi": None}


def get_token_tokenomics(symbol):
    """surf token-tokenomics → 代币解锁（只对 B+ 候选，1次调用）"""
    data = surf_cmd(f'token-tokenomics --symbol={symbol}', timeout=20)
    if data and isinstance(data, dict):
        items = data.get("data", [])
        if items:
            now = int(datetime.now().timestamp())
            upcoming = sum(
                t.get("unlock_amount", 0)
                for t in items
                if 0 < int(t.get("timestamp", 0)) <= now + 30 * 86400
            )
            return {"has_data": True, "total_unlocks": len(items), "upcoming_30d": upcoming, "events": items[:3]}
    return {"has_data": False, "total_unlocks": 0, "upcoming_30d": 0, "events": []}


# ====== v6 新增：Arkham/Surf 筹码追踪 ======

def _classify_by_entity(entity_type, entity_name, labels_list):
    """根据 Arkham entity_type 分类交易对手
    entity_type: 来自 from_label.to_label 的 entity_type 字段
    返回: "exchange" | "vc" | "whale" | "dex" | "unknown"
    """
    if not entity_type:
        # fallback: 检查 entity_name 和 labels
        name_lower = (entity_name or "").lower()
        label_texts = [lb.get("label", "").lower() for lb in (labels_list or [])]
        combined = name_lower + " " + " ".join(label_texts)
        if any(ex in combined for ex in ["binance", "coinbase", "kraken", "okx", "bybit", "huobi", "bitget", "mexc", "gate", "kucoin"]):
            return "exchange"
        if any(vc in combined for vc in ["a16z", "paradigm", "sequoia", "coinbase ventures", "binance labs", "polychain", "alameda", "three arrows", "jump"]):
            return "vc"
        if "whale" in combined or "wintermute" in combined:
            return "whale"
        return "unknown"

    et = entity_type.lower()
    if et in EXCHANGE_TYPES:
        return "exchange"
    if et in VC_TYPES:
        return "vc"
    if et in WHALE_TYPES:
        return "whale"
    if et in DEX_TYPES:
        return "dex"
    return "unknown"


def load_smart_money_state():
    """加载历史状态，用于比较筹码变化"""
    try:
        with open(SMART_MONEY_STATE) as f:
            return json.load(f)
    except:
        return {}


def save_smart_money_state(state):
    with open(SMART_MONEY_STATE, "w") as f:
        json.dump(state, f, ensure_ascii=False)


def get_token_transfer_flow(address, chain="ethereum", limit=50):
    """
    v6 核心：用 surf token-transfers --include=labels 获取代币链上转账流
    直接利用 Arkham entity_type 分类，不需要额外查 wallet-labels-batch

    返回: {
        "exchange_in_count": int,   # 往交易所转（卖出压力）
        "exchange_out_count": int,  # 从交易所转出（买入/做市）
        "vc_in_count": int,         # VC 买入（建仓信号）
        "vc_out_count": int,        # VC 卖出（分配信号）
        "whale_in_count": int,      # 鲸鱼买入
        "whale_out_count": int,     # 鲸鱼卖出
        "dex_in_count": int,        # DEX 流入
        "dex_out_count": int,       # DEX 流出
        "signal": "accumulating" | "distributing" | "neutral" | "unknown",
        "top_in_wallets": [],        # [(address, type, count)]
        "top_out_wallets": [],
        "recent_transfers": [],      # 最近5笔详情
        "total_transfers_analyzed": int,
    }
    """
    default_result = {
        "exchange_in_count": 0, "exchange_out_count": 0,
        "vc_in_count": 0, "vc_out_count": 0,
        "whale_in_count": 0, "whale_out_count": 0,
        "dex_in_count": 0, "dex_out_count": 0,
        "signal": "unknown",
        "top_in_wallets": [], "top_out_wallets": [],
        "recent_transfers": [],
        "total_transfers_analyzed": 0,
    }

    if not address or len(address) < 10:
        return default_result

    # 调用 surf token-transfers（带 --include=labels 获取 Arkham 标签）
    raw = surf_cmd(f"token-transfers --address={address} --chain={chain} --limit={limit} --include=labels", timeout=30)
    if not raw or not isinstance(raw, dict):
        return default_result

    data = raw.get("data", [])
    if not data:
        return default_result

    # 统计
    ex_in, ex_out = 0, 0
    vc_in, vc_out = 0, 0
    whale_in, whale_out = 0, 0
    dex_in, dex_out = 0, 0

    # 按地址统计流入/流出次数（用于找 Top 钱包）
    wallet_in_counts = defaultdict(int)  # address -> count (incoming to this address)
    wallet_out_counts = defaultdict(int)  # address -> count (outgoing from this address)

    recent = []

    for t in data:
        from_label = t.get("from_label") or {}
        to_label = t.get("to_label") or {}
        from_type = _classify_by_entity(
            from_label.get("entity_type"), from_label.get("entity_name"), from_label.get("labels")
        )
        to_type = _classify_by_entity(
            to_label.get("entity_type"), to_label.get("entity_name"), to_label.get("labels")
        )
        from_addr = t.get("from_address", "")
        to_addr = t.get("to_address", "")
        flow = t.get("flow", "")

        # 统计
        if to_type == "exchange":
            ex_in += 1
            wallet_in_counts[to_addr] += 1
        elif from_type == "exchange":
            ex_out += 1
            wallet_out_counts[from_addr] += 1
        elif to_type == "vc":
            vc_in += 1
            wallet_in_counts[to_addr] += 1
        elif from_type == "vc":
            vc_out += 1
            wallet_out_counts[from_addr] += 1
        elif to_type == "whale":
            whale_in += 1
            wallet_in_counts[to_addr] += 1
        elif from_type == "whale":
            whale_out += 1
            wallet_out_counts[from_addr] += 1
        elif to_type == "dex":
            dex_in += 1
            wallet_in_counts[to_addr] += 1
        elif from_type == "dex":
            dex_out += 1
            wallet_out_counts[from_addr] += 1

        # 记录最近5笔 Arkham 有标签的大额转账
        if from_type != "unknown" or to_type != "unknown":
            recent.append({
                "from": from_addr[:10] + "...",
                "to": to_addr[:10] + "...",
                "from_type": from_type,
                "to_type": to_type,
                "symbol": t.get("symbol", ""),
            })

    # Top 流入/流出钱包（按次数而非 USD，因为没有 USD 数据）
    top_in = sorted(wallet_in_counts.items(), key=lambda x: -x[1])[:5]
    top_out = sorted(wallet_out_counts.items(), key=lambda x: -x[1])[:5]

    # Arkham 实体名（更易读）
    def _get_name(addr):
        for t in data:
            if t.get("from_address") == addr:
                lbl = t.get("from_label") or {}
                return lbl.get("entity_name") or addr[:10] + "..."
            if t.get("to_address") == addr:
                lbl = t.get("to_label") or {}
                return lbl.get("entity_name") or addr[:10] + "..."
        return addr[:10] + "..."

    top_in_wallets = [(_get_name(a), "in", c) for a, c in top_in if c >= 2]
    top_out_wallets = [(_get_name(a), "out", c) for a, c in top_out if c >= 2]

    # 信号判断
    # 逻辑：VC/鲸鱼 买入 + 交易所净流出 = 吸筹
    #       交易所 净流入 = 抛压
    net_exchange = ex_in - ex_out
    smart_money_score = (vc_in * 3 + whale_in * 2) - (vc_out * 3 + whale_out * 2)

    if vc_in >= 2 or whale_in >= 3:
        if net_exchange <= 2:
            signal = "accumulating"
        else:
            signal = "accumulating"  # 强信号覆盖
    elif ex_in > 5 and vc_in == 0 and whale_in == 0:
        signal = "distributing"
    elif net_exchange > 5 and smart_money_score < 0:
        signal = "distributing"
    elif ex_in > ex_out * 2 and ex_in > 3:
        signal = "distributing"
    elif smart_money_score > 3:
        signal = "accumulating"
    elif smart_money_score < -3:
        signal = "distributing"
    else:
        signal = "neutral"

    result = {
        "exchange_in_count": ex_in,
        "exchange_out_count": ex_out,
        "vc_in_count": vc_in,
        "vc_out_count": vc_out,
        "whale_in_count": whale_in,
        "whale_out_count": whale_out,
        "dex_in_count": dex_in,
        "dex_out_count": dex_out,
        "signal": signal,
        "top_in_wallets": top_in_wallets,
        "top_out_wallets": top_out_wallets,
        "recent_transfers": recent[:5],
        "total_transfers_analyzed": len(data),
    }

    # ===== v6.2 新增：DexScreener buy/sell 次数补充（Arkham 无数据时）=====
    # Arkham 的 ex_in/ex_out 是"币转交易所"，DexScreener 的 buys/sells 是"链上真实买卖"
    # 两者互补，Arkham = 筹码流向，DexScreener buys = 散户/聪明钱买入压力
    # 条件：Arkham 没有交易所流向数据时（ex_in==0 AND ex_out==0）就用 DexScreener 补充
    if ex_in == 0 and ex_out == 0:
        dex_data = _get_dexscreener_transfer_data(address, chain)
        if dex_data:
            result["dex_buy_24h"] = dex_data["buys"]
            result["dex_sell_24h"] = dex_data["sells"]
            result["dex_buy_sell_ratio"] = dex_data["ratio"]
            result["signal"] = dex_data["signal"]
            result["top_in_wallets"] = dex_data.get("top_buyers", [])
            result["total_transfers_analyzed"] = dex_data.get("total_txs", 0)

    return result


# ═══════════════════════════════════════════════════════════════════════
# v7 新增：HertzFlow 筹码三分法 + Dumper 分类 + Proxy 检测
# 方法论来源：融合 binance-alpha-chip-analysis skill
# 适用：Alpha 阶段代币（未毕业）
# ═══════════════════════════════════════════════════════════════════════

def _hftz_chain_alias(chain):
    """Surf chain 参数映射"""
    mapping = {
        "ethereum": "ethereum",
        "bsc": "bsc",
        "base": "base",
        "solana": "solana",
        "polygon": "polygon",
        "arbitrum": "arbitrum",
        "optimism": "optimism",
        "avalanche": "avalanche",
        # 常见别名
        "eth": "ethereum",
        "avax": "avalanche",
        "matic": "polygon",
        "arb": "arbitrum",
        "op": "optimism",
    }
    return mapping.get(chain.lower(), chain.lower())


def hftz_get_token_holders(address, chain="ethereum", limit=50):
    """
    HertzFlow Rule 4：Surf token-holders 筹码三分法
    返回: {
        "operator_pct": float,     # Operator（Proxy/Deployer/Team）持币比例
        "cex_pool_pct": float,      # CEX Pool 比例
        "verifiable_retail_pct": float,  # 真实可检测散户
        "operator_addresses": [(addr, pct, label)],
        "cex_addresses": [(addr, pct, label)],
        "total_analyzed": int,
        "holder_count": int,
    }
    """
    result = {
        "operator_pct": 0.0, "cex_pool_pct": 0.0, "verifiable_retail_pct": 0.0,
        "operator_addresses": [], "cex_addresses": [],
        "total_analyzed": 0.0, "holder_count": 0,
    }
    if not address:
        return result

    c_chain = _hftz_chain_alias(chain)
    raw = surf_cmd(f"token-holders --address={address} --chain={c_chain} --limit={limit}", timeout=20)
    if not raw or not isinstance(raw, dict):
        return result

    # 解析 holders（Surf 输出格式：.data[]）
    holders = raw.get("data", []) or []
    if not holders:
        return result

    total_supply = 0.0
    operator_tokens = 0.0
    cex_tokens = 0.0
    holder_count = 0
    operator_addrs = []
    cex_addrs = []

    for h in holders:
        # 提取地址和余额（Surf token-holders 格式：address/balance/percentage）
        addr = h.get("address", "") or ""
        raw_balance = h.get("balance", "0")
        try:
            balance = float(raw_balance)
        except (ValueError, TypeError):
            balance = 0.0

        pct_str = h.get("percentage", None)
        try:
            pct = float(pct_str) if pct_str else 0.0
        except (ValueError, TypeError):
            pct = 0.0

        # 提取标签（Surf token-holders 无 entity_type，只有 entity_name/labels）
        labels = h.get("labels", []) or []
        label_texts = [lb.get("label", "").lower() for lb in labels]
        entity_type = h.get("entity_type", "") or ""
        entity_name = h.get("entity_name", "") or ""
        combined = (entity_name + " " + " ".join(label_texts)).lower()

        holder_count += 1

        # Operator 检测（Proxy 合约 / Deployer / Team）
        # Surf token-holders 无 entity_type，用关键词判断
        is_operator = False
        if entity_type in ("contract", "proxy", "distributor"):
            is_operator = True
        if any(kw in combined for kw in ["proxy", "distributor", "locker", "team wallet", "deployer", "treasury", "foundation", "zerohex", "burn"]):
            is_operator = True
        # CEX 检测（无 entity_type，靠名称）
        is_cex = False
        if entity_type in ("cex", "exchange"):
            is_cex = True
        if any(ex in combined for ex in ["binance", "coinbase", "kraken", "okx", "bybit", "huobi", "bitget", "mexc", "gate", "kucoin", " BIN", "CB"]):
            is_cex = True
        # 若无任何标签但余额极大（靠前5），归为 Operator（可能是 Proxy/Team）
        if not entity_name and not label_texts and holder_count <= 3 and balance > 0:
            is_operator = True  # 靠前的无标签大持仓 = 团队/Proxy

        if is_operator:
            operator_tokens += pct  # 用 percentage 字段（已是对总供应量的百分比）
            operator_addrs.append((addr, pct, entity_name or "Contract"))
        elif is_cex:
            cex_tokens += pct  # 用 percentage 字段
            cex_addrs.append((addr, pct, entity_name or "CEX"))
        total_supply += pct  # percentage 加总 = 100%（近似）

    if total_supply > 0:
        # percentage 直接就是 %，不需要除以 total_supply
        result["operator_pct"] = operator_tokens  # 已是百分比
        result["cex_pool_pct"] = cex_tokens      # 已是百分比
        result["verifiable_retail_pct"] = max(0.0, 100.0 - result["operator_pct"] - result["cex_pool_pct"])
        result["operator_addresses"] = sorted(operator_addrs, key=lambda x: -x[1])[:5]
        result["cex_addresses"] = sorted(cex_addrs, key=lambda x: -x[1])[:5]
        result["total_analyzed"] = total_supply
        result["holder_count"] = holder_count

    return result


def hftz_get_proxy_distributor(address, chain="ethereum", limit=80):
    """
    HertzFlow Rule 7：检测 Proxy Distributor 分发合约
    识别分发路径：Proxy → 中转地址 → CEX/DEX

    对 BSC 链：由于 token-transfers --include=labels 返回无标签，
    改用 wallet-labels-batch 批量查询转账中的地址标签。

    返回: {
        "has_proxy": bool,
        "proxy_address": str,
        "proxy_type": str,      # "sablier" | "vesting" | "linear" | "unknown"
        "total_distributed": float,
        "distribution_count": int,
        "cex_destination_count": int,
        "distribution_addresses": [(addr, count, dest_type)],
        "signal": str,  # "active_distribution" | "quiet" | "none"
    }
    """
    result = {
        "has_proxy": False, "proxy_address": "", "proxy_type": "unknown",
        "total_distributed": 0.0, "distribution_count": 0,
        "cex_destination_count": 0,
        "distribution_addresses": [],
        "signal": "none",
    }
    if not address:
        return result

    c_chain = _hftz_chain_alias(chain)
    raw = surf_cmd(f"token-transfers --address={address} --chain={c_chain} --limit={limit}", timeout=30)
    if not raw or not isinstance(raw, dict):
        return result

    transfers = raw.get("data", []) or []
    if not transfers:
        return result

    # 提取所有涉及的地址
    all_addresses = set()
    for t in transfers:
        all_addresses.add(t.get("from_address", ""))
        all_addresses.add(t.get("to_address", ""))

    # 批量查询 Arkham 标签（如果有标签数据，直接用 from_label）
    addr_labels = {}  # addr -> {"entity_type": str, "entity_name": str}
    labeled_transfers = transfers

    # 先尝试从 transfers 的 from_label/to_label 获取（EVM 链有数据时有效）
    has_native_labels = any(
        t.get("from_label") or t.get("to_label") for t in transfers
    )
    if has_native_labels:
        # Arkham 有标签，直接用（ETH/Base）
        pass
    elif chain.lower() in ("bsc", "base", "ethereum") and len(all_addresses) <= 50:
        # BSC 等链无原生标签，批量查询 wallet-labels-batch
        addr_list = list(all_addresses)[:50]
        batch_result = surf_cmd(
            f"wallet-labels-batch --addresses={','.join(addr_list)}",
            timeout=20
        )
        if batch_result and isinstance(batch_result, dict):
            for item in (batch_result.get("data", []) or []):
                a = item.get("address", "").lower()
                addr_labels[a] = {
                    "entity_type": item.get("entity_type", "") or "",
                    "entity_name": item.get("entity_name", "") or "",
                    "labels": item.get("labels", []) or [],
                }

    def get_label(addr):
        return addr_labels.get(addr.lower(), {}) if addr_labels else {}

    def is_contract_type(entity_type, entity_name, labels_text):
        combined = (entity_name + " " + labels_text).lower()
        return (
            entity_type in ("contract", "proxy", "distributor")
            or any(kw in combined for kw in ["proxy", "distributor", "locker", "vesting", "sablier", "team wallet", "deployer", "treasury"])
        )

    def is_cex_type(entity_type, entity_name, labels_text):
        combined = (entity_name + " " + labels_text).lower()
        return (
            entity_type in ("cex", "exchange")
            or any(ex in combined for ex in ["binance", "coinbase", "kraken", "okx", "bybit", "huobi", "bitget", "mexc", "gate", "kucoin"])
        )

    # 统计各 from_address 的发出量
    from_stats = defaultdict(lambda: {"amount": 0.0, "count": 0, "destinations": defaultdict(int)})
    for t in transfers:
        from_addr = t.get("from_address", "")
        to_addr = t.get("to_address", "")
        raw_amt = t.get("amount", "0")
        try:
            amt = abs(float(raw_amt))
        except:
            amt = 0.0
        from_stats[from_addr]["amount"] += amt
        from_stats[from_addr]["count"] += 1

        # to_addr 标签
        lbl = get_label(to_addr)
        et = lbl.get("entity_type", "")
        en = lbl.get("entity_name", "")
        lt = " ".join([lb.get("label","") for lb in lbl.get("labels", [])]).lower()
        combined = (en + " " + lt).lower()

        if is_cex_type(et, en, lt):
            from_stats[from_addr]["destinations"]["cex"] += 1
        elif is_contract_type(et, en, lt):
            from_stats[from_addr]["destinations"]["contract"] += 1
        else:
            from_stats[from_addr]["destinations"]["wallet"] += 1

    # 找最大分发者（按 amount）
    if not from_stats:
        return result

    # 找合约类型（Proxy/Contract）的分发者
    contract_senders = {}
    for addr, stats in from_stats.items():
        lbl = get_label(addr)
        et = lbl.get("entity_type", "")
        en = lbl.get("entity_name", "")
        lt = " ".join([lb.get("label","") for lb in lbl.get("labels", [])]).lower()
        combined = (en + " " + lt).lower()

        if is_contract_type(et, en, lt):
            contract_senders[addr] = stats

    if not contract_senders:
        return result

    # 取最大分发量的合约
    proxy_addr = max(contract_senders, key=lambda a: contract_senders[a]["amount"])
    proxy_stats = contract_senders[proxy_addr]
    cex_count = proxy_stats["destinations"].get("cex", 0)
    other_count = proxy_stats["destinations"].get("contract", 0) + proxy_stats["destinations"].get("wallet", 0)

    # Proxy 类型识别
    proxy_type = "unknown"
    lbl = get_label(proxy_addr)
    en = (lbl.get("entity_name", "") + " " + " ".join([lb.get("label","") for lb in lbl.get("labels", [])])).lower()
    if "sablier" in en:
        proxy_type = "sablier"
    elif "vesting" in en or "locker" in en:
        proxy_type = "vesting"
    elif "linear" in en:
        proxy_type = "linear"

    signal = "quiet"
    if cex_count >= 2:
        signal = "active_distribution"
    elif other_count >= 4:
        signal = "quiet"

    result = {
        "has_proxy": True,
        "proxy_address": proxy_addr,
        "proxy_type": proxy_type,
        "total_distributed": proxy_stats["amount"],
        "distribution_count": proxy_stats["count"],
        "cex_destination_count": cex_count,
        "distribution_addresses": sorted(proxy_stats["destinations"].items(), key=lambda x: -x[1]),
        "signal": signal,
    }
    return result


def hftz_classify_dumper(address, chain="ethereum", limit=80):
    """
    HertzFlow Rule 8 + Rule 10：Quiet / Partial / Full Dumper 分类

    对 BSC 链：由于 token-transfers --include=labels 返回无标签，
    用 wallet-labels-batch 批量查询转账中的目的地址标签。

    返回: {
        "dumper_class": str,   # "quiet" | "partial" | "full" | "none"
        "cex_transfer_ratio": float,  # CEX 转账占总转账比例
        "estimated_sold_pct": float,
        "cex_net_flow": int,
        "signal": str,
        "verdict": str,
    }
    """
    result = {
        "dumper_class": "none",
        "cex_transfer_ratio": 0.0,
        "estimated_sold_pct": 0.0,
        "cex_net_flow": 0,
        "signal": "no_dumping",
        "verdict": "HOLD",
    }
    if not address:
        return result

    c_chain = _hftz_chain_alias(chain)
    raw = surf_cmd(f"token-transfers --address={address} --chain={c_chain} --limit={limit}", timeout=30)
    if not raw or not isinstance(raw, dict):
        return result

    transfers = raw.get("data", []) or []
    if not transfers:
        return result

    total_txs = len(transfers)

    # 收集所有目的地址
    to_addresses = set(t.get("to_address", "") for t in transfers)

    # 批量查询标签
    addr_labels = {}
    if chain.lower() in ("bsc", "base", "ethereum") and len(to_addresses) <= 50:
        addr_list = list(to_addresses)[:50]
        batch_result = surf_cmd(
            f"wallet-labels-batch --addresses={','.join(addr_list)}",
            timeout=20
        )
        if batch_result and isinstance(batch_result, dict):
            for item in (batch_result.get("data", []) or []):
                a = item.get("address", "").lower()
                addr_labels[a] = {
                    "entity_type": item.get("entity_type", "") or "",
                    "entity_name": item.get("entity_name", "") or "",
                    "labels": item.get("labels", []) or [],
                }

    def is_cex_addr(addr):
        lbl = addr_labels.get(addr.lower(), {})
        et = lbl.get("entity_type", "") or ""
        en = lbl.get("entity_name", "") or ""
        lt = " ".join([lb.get("label","") for lb in lbl.get("labels", [])]).lower()
        combined = (en + " " + lt).lower()
        return (
            et in ("cex", "exchange")
            or any(ex in combined for ex in ["binance", "coinbase", "kraken", "okx", "bybit", "huobi", "bitget", "mexc", "gate", "kucoin"])
        )

    cex_in_txs = 0
    cex_out_txs = 0

    for t in transfers:
        from_addr = t.get("from_address", "")
        to_addr = t.get("to_address", "")

        if is_cex_addr(from_addr):
            cex_out_txs += 1
        if is_cex_addr(to_addr):
            cex_in_txs += 1

    cex_net = cex_in_txs - cex_out_txs
    cex_ratio = cex_in_txs / total_txs if total_txs > 0 else 0.0

    dumper_class = "none"
    verdict = "HOLD"
    signal = "no_dumping"

    if cex_in_txs == 0:
        result["dumper_class"] = "none"
        result["cex_transfer_ratio"] = 0.0
        result["cex_net_flow"] = 0
        result["signal"] = "no_dumping"
        result["verdict"] = "HOLD"
        return result

    if cex_ratio < 0.30:
        dumper_class = "quiet"
        verdict = "ACCUMULATE"
        signal = "quiet_dumping"
    elif cex_ratio < 0.70:
        dumper_class = "partial"
        verdict = "REDUCE" if cex_net > 5 else "WATCH"
        signal = "partial_dumping"
    else:
        dumper_class = "full"
        verdict = "EXIT_ALL"
        signal = "full_dumping"

    result = {
        "dumper_class": dumper_class,
        "cex_transfer_ratio": cex_ratio * 100,
        "estimated_sold_pct": cex_ratio * 100 * 0.5,
        "cex_net_flow": cex_net,
        "signal": signal,
        "verdict": verdict,
    }
    return result


def hftz_run_chip_analysis(address, chain="ethereum"):
    """
    HertzFlow v7 主入口：对单个代币运行完整筹码分析
    融合：持仓三分法 + Proxy 检测 + Dumper 分类
    返回: {
        "holders": dict,       # hftz_get_token_holders 结果
        "proxy": dict,        # hftz_get_proxy_distributor 结果
        "dumper": dict,        # hftz_classify_dumper 结果
        "combined_signal": str,  # "accumulating" | "distributing" | "warning" | "neutral"
        "verdict": str,         # "BUY" | "HOLD" | "REDUCE" | "EXIT_ALL" | "WATCH"
        "hftz_score": int,      # 0-100 HertzFlow 筹码评分
        "risk_flags": [str],    # 风险标注
    }
    """
    import concurrent.futures

    combined = {
        "holders": {},
        "proxy": {"has_proxy": False, "signal": "none"},
        "dumper": {"dumper_class": "none", "signal": "no_dumping"},
        "combined_signal": "neutral",
        "verdict": "WATCH",
        "hftz_score": 50,
        "risk_flags": [],
    }

    # 并发拉取三个维度的数据
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        fh = ex.submit(hftz_get_token_holders, address, chain, 50)
        fp = ex.submit(hftz_get_proxy_distributor, address, chain, 80)
        fd = ex.submit(hftz_classify_dumper, address, chain, 80)

        try:
            holders_data = fh.result(timeout=25)
            combined["holders"] = holders_data
        except Exception:
            combined["holders"] = {}

        try:
            proxy_data = fp.result(timeout=25)
            combined["proxy"] = proxy_data
        except Exception:
            combined["proxy"] = {"has_proxy": False, "signal": "none"}

        try:
            dumper_data = fd.result(timeout=25)
            combined["dumper"] = dumper_data
        except Exception:
            combined["dumper"] = {"dumper_class": "none", "signal": "no_dumping"}

    # 综合判断
    h_score = 50
    risk_flags = []
    signals = []

    holders = combined["holders"]
    op_pct = holders.get("operator_pct", 0.0)
    cex_pct = holders.get("cex_pool_pct", 0.0)

    # Rule 4 - 筹码三分法评分
    if op_pct > 50:
        h_score -= 20
        risk_flags.append(f"Operator持仓过高({op_pct:.1f}%)")
    elif op_pct > 35:
        h_score -= 10
        risk_flags.append(f"Operator持仓偏高({op_pct:.1f}%)")
    elif op_pct < 15:
        h_score += 10

    if cex_pct > 40:
        h_score -= 15
        risk_flags.append(f"CEX Pool偏高({cex_pct:.1f}%)，注意砸盘")
    elif cex_pct > 25:
        h_score -= 5

    # Rule 7 - Proxy 分发信号
    proxy_sig = combined["proxy"].get("signal", "none")
    if proxy_sig == "active_distribution":
        h_score -= 15
        risk_flags.append("Proxy活跃分发→CEX（派现中）")
        signals.append("distributing")
    elif proxy_sig == "quiet":
        h_score += 5
        signals.append("quiet")

    # Rule 8 - Dumper 分类
    dumper_class = combined["dumper"].get("dumper_class", "none")
    dumper_sig = combined["dumper"].get("signal", "no_dumping")
    cex_ratio = combined["dumper"].get("cex_transfer_ratio", 0.0)

    if dumper_class == "full":
        h_score -= 25
        risk_flags.append("Full Dumper（大量→CEX，砸盘）")
        signals.append("distributing")
    elif dumper_class == "partial":
        h_score -= 10
        risk_flags.append(f"Partial Dumper（CEX渗透率{cex_ratio:.0f}%）")
        signals.append("distributing")
    elif dumper_class == "quiet":
        h_score += 5   # Quiet Dumper 不压价，反而可能是庄家吸筹
        risk_flags.append("Quiet Dumper（微量持续派发，注意跟进）")
        signals.append("accumulating")
    else:
        signals.append("neutral")

    # 综合信号
    dist_count = sum(1 for s in signals if s == "distributing")
    accum_count = sum(1 for s in signals if s == "accumulating")

    if dist_count >= 2:
        combined_signal = "distributing"
        verdict = "EXIT_ALL" if dumper_class == "full" else "REDUCE"
    elif accum_count >= 1 and dist_count == 0:
        combined_signal = "accumulating"
        verdict = "BUY"
    elif dist_count == 1:
        combined_signal = "warning"
        verdict = "REDUCE"
    else:
        combined_signal = "neutral"
        verdict = "WATCH"

    combined["combined_signal"] = combined_signal
    combined["verdict"] = verdict
    combined["hftz_score"] = max(0, min(100, h_score))
    combined["risk_flags"] = risk_flags

    return combined


def _get_dexscreener_transfer_data(address, chain="ethereum"):
    """
    DexScreener /tokens/{address} → 获取 24h buy/sell 次数
    补充 Arkham 未覆盖的新币（尤其是 BSC/Base 新上线币）
    返回: {
        "buys": int, "sells": int, "ratio": float,
        "signal": str,  # "accumulating" | "distributing" | "neutral"
        "top_buyers": [(name, count)],  # DEX 买家地址标签
        "total_txs": int
    }
    """
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{address}",
            timeout=10,
            headers={"Accept": "application/json"}
        )
        if r.status_code != 200:
            return None
        data = r.json()
        pairs = data.get("pairs") or []
        if isinstance(pairs, list):
            pairs = {p.get("chainId"): p for p in pairs} if False else pairs

        # 取最大流动性的 pair
        best = None
        for p in (pairs if isinstance(pairs, list) else []):
            if not p or not p.get("priceUsd"):
                continue
            liq = float(p.get("liquidity", {}).get("usd", 0) or 0)
            if not best or liq > float(best.get("liquidity", {}).get("usd", 0)):
                best = p

        if not best:
            return None

        txns = best.get("txns", {}) or {}
        buys = int(txns.get("h24", {}).get("buys", 0) or 0)
        sells = int(txns.get("h24", {}).get("sells", 0) or 0)
        total = buys + sells
        ratio = (buys / sells) if sells > 0 else (float('inf') if buys > 0 else 1.0)

        # 信号判断（基于 buy/sell 比）
        if total < 5:
            sig = "neutral"
        elif ratio >= 2.0:
            sig = "accumulating"    # 买入压倒性
        elif ratio <= 0.5:
            sig = "distributing"    # 卖出压倒性
        elif buys > sells:
            sig = "accumulating"
        elif sells > buys:
            sig = "distributing"
        else:
            sig = "neutral"

        # 从 pair label 找交易所/DEX 信息
        mkt = best.get("market", {}) or {}
        dex_name = best.get("dexId", "unknown")
        liquidity = float(best.get("liquidity", {}).get("usd", 0) or 0)

        # 找 Top 买家（从 label 字段）
        top_buyers = [(dex_name, buys)] if buys > 0 else []

        return {
            "buys": buys,
            "sells": sells,
            "ratio": ratio,
            "signal": sig,
            "top_buyers": top_buyers,
            "total_txs": total,
            "liquidity_usd": liquidity,
            "chain": best.get("chainId", chain),
        }
    except Exception:
        return None


def get_token_holders_snapshot(address, chain="ethereum"):
    """
    获取代币持有者快照，对比历史状态检测建仓/出货
    返回: {"current_holders": int, "signal": "new"|"accumulating"|"distributing"|"stable", "change_pct": float}
    """
    data = surf_cmd(f"token-holders --address={address} --chain={chain} --limit=1", timeout=20)
    if not data or not isinstance(data, dict):
        return {"current_holders": 0, "signal": "unknown", "change_pct": 0}

    items = data.get("data", [])
    meta = data.get("meta", {})
    total = meta.get("total", 0)
    return {"current_holders": total, "signal": "unknown", "change_pct": 0}


def analyze_smart_money_historical(symbol, current_flow):
    """
    对比历史状态，判断筹码趋势
    """
    state = load_smart_money_state()
    prev = state.get(symbol, {})
    prev_signal = prev.get("last_signal", "unknown")
    prev_net_ex = prev.get("net_exchange", 0)
    curr_net_ex = (current_flow.get("exchange_in_count", 0) - current_flow.get("exchange_out_count", 0))

    # 更新状态
    state[symbol] = {
        "last_signal": current_flow.get("signal", "unknown"),
        "net_exchange": curr_net_ex,
        "vc_in": current_flow.get("vc_in_count", 0),
        "vc_out": current_flow.get("vc_out_count", 0),
        "whale_in": current_flow.get("whale_in_count", 0),
        "whale_out": current_flow.get("whale_out_count", 0),
        "exchange_in": current_flow.get("exchange_in_count", 0),
        "exchange_out": current_flow.get("exchange_out_count", 0),
    }
    save_smart_money_state(state)

    # 信号强化：如果历史和当前一致，信号更可靠
    signal = current_flow.get("signal", "unknown")
    if signal == "accumulating" and prev_signal == "accumulating":
        signal = "accumulating_confirmed"
    elif signal == "distributing" and prev_signal == "distributing":
        signal = "distributing_confirmed"
    return signal


# ====== Binance API（保留核心功能）======

ALPHA_API = "https://www.binance.com/bapi/defi/v1/public/alpha-trade/aggTicker24?dataType=aggregate"


def get_coinbase_premium():
    """机构溢价指数"""
    try:
        binance_r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=5)
        binance_btc = float(binance_r.json().get("price", 0))
        kraken_r = requests.get("https://api.kraken.com/0/public/Ticker?pair=XBTUSD", timeout=5)
        kraken_btc = 0
        if kraken_r.status_code == 200:
            kraken_data = kraken_r.json().get("result", {})
            btc_data = kraken_data.get("XXBTZUSD", {})
            if isinstance(btc_data, dict) and 'a' in btc_data:
                kraken_btc = float(btc_data['a'][0])
        if binance_btc > 0 and kraken_btc > 0:
            premium_pct = ((binance_btc - kraken_btc) / kraken_btc) * 100
            return {
                "binance_btc": binance_btc,
                "kraken_btc": kraken_btc,
                "premium_pct": premium_pct,
                "signal": "机构买入中" if premium_pct < -0.5 else ("机构卖出中" if premium_pct > 0.5 else "中性")
            }
    except Exception as e:
        print(f"[WARN] 溢价失败: {e}")
    return None


def get_volume_anomaly(symbol):
    """Binance K线成交量异动（快速，直接调 Binance API）"""
    try:
        kline_url = f"https://api.binance.com/api/v3/klines?symbol={symbol}USDT&interval=1d&limit=30"
        r = requests.get(kline_url, timeout=10)
        klines = r.json()
        if not klines or len(klines) < 10:
            return None
        volumes = [float(k[5]) for k in klines[-30:]]
        avg_vol = sum(volumes) / len(volumes)
        anomalies = []
        for k in klines[-7:]:
            ts = int(k[0])
            open_p = float(k[1])
            close_p = float(k[4])
            vol = float(k[5])
            if vol > avg_vol * 3:
                pc = ((close_p - open_p) / open_p * 100) if open_p > 0 else 0
                anomalies.append({
                    "date": datetime.fromtimestamp(ts / 1000).strftime("%m-%d"),
                    "volume": vol,
                    "avg_vol": avg_vol,
                    "volume_ratio": vol / avg_vol,
                    "price_change": pc,
                    "type": "对敲换手" if abs(pc) < 10 else ("放量拉升" if pc > 10 else "放量下跌")
                })
        return anomalies if anomalies else None
    except:
        return None


def get_dex_price(address):
    """DexScreener 实时价格"""
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{address}", timeout=8)
        pairs = r.json().get("pairs", [])
        valid = [p for p in pairs if p.get("priceUsd") and float(p.get("liquidity", {}).get("usd", 0)) > 1000]
        if valid:
            best = max(valid, key=lambda x: float(x.get("liquidity", {}).get("usd", 0)))
            return {
                "price": float(best["priceUsd"]),
                "change24h": float(best["priceChange"]["h24"]),
                "liquidity": float(best["liquidity"].get("usd", 0)),
                "chain": best.get("chainId", "unknown")
            }
    except:
        pass
    return None


def get_token_risk(chain, address):
    """DexScreener 风险标签"""
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{address}", timeout=8)
        pairs = r.json().get("pairs", [])
        valid = [p for p in pairs if p.get("priceUsd") and float(p.get("liquidity", {}).get("usd", 0)) > 1000]
        if not valid:
            return {"risk_level": "UNKNOWN", "tags": []}
        best = max(valid, key=lambda x: float(x.get("liquidity", {}).get("usd", 0)))
        mcap = float(best.get("marketCap") or 0)
        liquidity = float(best.get("liquidity", {}).get("usd", 0))
        tags = []
        if liquidity > 0 and mcap > 0:
            ratio = liquidity / mcap
            if ratio < 0.02:
                tags.append("低流动性")
            elif ratio < 0.05:
                tags.append("流动性紧张")
        return {"risk_level": "MID" if tags else "LOW", "tags": tags}
    except:
        return {"risk_level": "UNKNOWN", "tags": []}


def get_alpha_list():
    """Binance Alpha 列表"""
    headers = {"lang": "zh", "User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(ALPHA_API, headers=headers, timeout=15)
        d = r.json()
        if d.get("code") == "000000":
            return d.get("data", [])
    except Exception as e:
        print(f"[ERROR] API失败: {e}")
    return []


# ====== 状态管理 ======

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {"reported_tokens": [], "last_scores": {}}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False)


def load_holder_state():
    try:
        with open(HOLDER_STATE_FILE) as f:
            return json.load(f)
    except:
        return {}


def save_holder_state(state):
    with open(HOLDER_STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False)


def detect_holder_signal(symbol, current_holders):
    try:
        current = int(current_holders) if str(current_holders).isdigit() else 0
    except:
        current = 0
    holder_state = load_holder_state()
    prev = holder_state.get(symbol)
    if prev is None:
        holder_state[symbol] = {"holders": current, "trend": "new"}
        save_holder_state(holder_state)
        return "new", 0
    prev_holders = prev.get("holders", 0)
    if current > prev_holders:
        cp = ((current - prev_holders) / prev_holders * 100) if prev_holders > 0 else 0
        signal = "accumulating" if cp > 5 else "slight_growth"
    elif current < prev_holders:
        cp = ((prev_holders - current) / prev_holders * 100) if prev_holders > 0 else 0
        signal = "distributing"
    else:
        cp = 0
        signal = "stable"
    holder_state[symbol] = {"holders": current, "trend": signal}
    save_holder_state(holder_state)
    return signal, cp


def load_replay():
    try:
        with open(REPLAY_FILE) as f:
            return json.load(f)
    except:
        return {}


def save_replay(replay):
    with open(REPLAY_FILE, "w") as f:
        json.dump(replay, f, ensure_ascii=False)


def update_replay(symbol, direction_tag, score, price_at_signal, timestamp, token=None):
    """
    保存/更新信号追踪记录。
    首次出现时：抓 surf token-holders 快照（启动前筹码结构），
    用于复盘时对比暴涨前后的筹码变化。
    """
    replay = load_replay()
    key = f"{symbol}_{timestamp}"

    record = {
        "symbol": symbol, "direction_tag": direction_tag,
        "score": score, "price_at_signal": price_at_signal,
        "signal_time": timestamp, "checkpoints": {}
    }

    # 信号时抓 holder 快照（启动前筹码）
    if token and key not in replay:
        addr = token.get("address", "")
        chain_raw = token.get("chain", "ethereum")
        # Alpha API chainId: 56=BSC, 1=ETH, 8453=Base, 42161=Arbitrum
        chain_map = {"56": "bsc", "1": "ethereum", "8453": "base", "42161": "arbitrum",
                     "bsc": "bsc", "ethereum": "ethereum", "base": "base", "arbitrum": "arbitrum"}
        chain_surf = chain_map.get(str(chain_raw), "ethereum")
        if addr and len(addr) == 42:
            holders_data = hftz_get_token_holders(addr, chain_surf, limit=50)
            if holders_data:
                record["snapshot"] = {
                    "top5_ratio": holders_data.get("top5_ratio", 0),
                    "top10_ratio": holders_data.get("top10_ratio", 0),
                    "operator_pct": holders_data.get("operator_pct", 0),
                    "cex_pool_pct": holders_data.get("cex_pool_pct", 0),
                    "dex_pool_pct": holders_data.get("dex_pool_pct", 0),
                    "top1_pct": holders_data.get("top1_pct", 0),
                    "top1_label": holders_data.get("top1_label", ""),
                    "total_holders": holders_data.get("total_holders", 0),
                    "chain": chain_surf,
                    "address": addr,
                }

    replay[key] = record
    save_replay(replay)


# ====== 评分引擎 v6 ======

def calculate_signal_score(token_data, volume_anomalies, premium_data, holder_trend,
                          tech_indicators, tokenomics, fear_greed,
                          smart_money_flow, smart_money_signal):
    dims = {k: 0 for k in SCORE_WEIGHTS}
    penalties = []

    # 1. 资金强度 (0-20)
    score = float(token_data.get("score", 0))
    dims["资金强度"] = 20 if score >= 111 else (16 if score >= 110 else (12 if score >= 105 else 8))

    # 2. 地址质量 (0-15)
    holders = token_data.get("holders", 0)
    try:
        holders = int(holders)
    except:
        holders = 0
    if holders >= 100000:
        dims["地址质量"] = 15
    elif holders >= 50000:
        dims["地址质量"] = 11
    elif holders >= 10000:
        dims["地址质量"] = 7
    elif holders >= 1000:
        dims["地址质量"] = 4
    else:
        dims["地址质量"] = 1
        penalties.append("持币地址过少")

    # 3. 市场确认 (0-15)
    market_score = 0
    try:
        price_change = float(token_data.get("priceChangePercent", 0))
    except:
        price_change = 0

    if volume_anomalies:
        拉升 = [a for a in volume_anomalies if a["type"] == "放量拉升"]
        对敲 = [a for a in volume_anomalies if a["type"] == "对敲换手"]
        下跌 = [a for a in volume_anomalies if a["type"] == "放量下跌"]
        if 拉升:
            market_score += 8
        elif 对敲 and price_change > -5:
            market_score += 5
        elif 下跌 and price_change < -10:
            market_score += 3
        rsi = tech_indicators.get("rsi")
        if rsi is not None:
            if 30 < rsi < 70:
                market_score += 2
            elif rsi <= 30 and price_change > 0:
                market_score += 4
            elif rsi >= 70 and price_change > 0:
                market_score -= 2
    else:
        market_score += 3

    if premium_data:
        prem = premium_data.get("premium_pct", 0)
        if prem < -0.5 and price_change > 0:
            market_score += 5
        elif prem > 0.5 and price_change < 0:
            market_score += 5
        elif abs(prem) < 0.3:
            market_score += 2

    fg_signal = fear_greed.get("signal", "Neutral")
    fg_value = fear_greed.get("value", 50)
    if fg_signal == "Fear" and price_change > 0:
        market_score += 3
    elif fg_signal == "Greed" and price_change > 30:
        market_score -= 3

    dims["市场确认"] = min(15, market_score)

    # 4. 催化权重 (0-10)
    catalyst_score = 5
    holder_sig, holder_pct = holder_trend
    if holder_sig == "accumulating":
        catalyst_score += 4
    elif holder_sig == "distributing":
        catalyst_score -= 3
    if tokenomics and tokenomics.get("has_data"):
        upcoming = tokenomics.get("upcoming_30d", 0)
        if upcoming > 0:
            catalyst_score -= 2
            penalties.append(f"30d解锁: {upcoming:,.0f}")
    dims["催化权重"] = max(0, min(10, catalyst_score))

    # 5. 流动性 (0-8)
    try:
        liquidity = float(token_data.get("liquidity", 0))
    except:
        liquidity = 0
    dims["流动性"] = 8 if liquidity >= 1_000_000 else (6 if liquidity >= 500_000 else (4 if liquidity >= 100_000 else (2 if liquidity >= 50_000 else 1)))
    if liquidity < 100_000:
        penalties.append("流动性不足")

    # 6. 时间优势 (0-7)
    change = abs(price_change)
    dims["时间优势"] = 7 if change < 5 else (5 if change < 15 else (3 if change < 30 else 1))
    if change >= 30:
        penalties.append("价格已大幅移动")

    # ====== v6 核心：筹码动向 (0-15) ======
    chip_score = 0
    smf = smart_money_flow or {}
    sm_signal = smart_money_signal or "unknown"
    vc_in = smf.get("vc_in_count", 0)
    vc_out = smf.get("vc_out_count", 0)
    whale_in = smf.get("whale_in_count", 0)
    whale_out = smf.get("whale_out_count", 0)
    ex_in = smf.get("exchange_in_count", 0)
    ex_out = smf.get("exchange_out_count", 0)
    net_exchange = ex_in - ex_out

    if sm_signal in ("accumulating", "accumulating_confirmed"):
        chip_score = 15 if sm_signal == "accumulating_confirmed" else 12
        if vc_in >= 3:
            chip_score += 3
        if whale_in >= 5:
            chip_score += 2
    elif sm_signal in ("distributing", "distributing_confirmed"):
        chip_score = -5 if sm_signal == "distributing_confirmed" else -3
        if ex_in > 5:
            chip_score -= 5
        penalties.append("筹码流出交易所")
    elif net_exchange > 3 and vc_in == 0:
        chip_score = 2
        penalties.append("交易所净流入偏多")
    elif net_exchange < -3:
        chip_score = 5
    elif net_exchange > 5:
        chip_score = -3
        penalties.append("交易所净流入偏多")
    else:
        chip_score = 5  # 中性

    dims["筹码动向"] = max(-5, min(15, chip_score))

    # 7. 风险结构 (0-10)
    risk = 10
    if liquidity < 100_000:
        risk -= 3
    if holders < 5000:
        risk -= 2
    if price_change > 50 or price_change < -30:
        risk -= 2
    if dims["筹码动向"] < 0:
        risk -= 2
    dims["风险结构"] = max(0, risk)

    # 扣分
    if volume_anomalies:
        下跌_a = [a for a in volume_anomalies if a["type"] == "放量下跌"]
        拉升_a = [a for a in volume_anomalies if a["type"] == "放量拉升"]
        if len(下跌_a) >= 2:
            penalties.append("连续放量下跌")
            dims["市场确认"] = max(0, dims["市场确认"] - 6)
        if len(拉升_a) >= 2:
            penalties.append("连续放量拉升")
            dims["时间优势"] = max(0, dims["时间优势"] - 2)

    # v6 额外扣分：筹码信号与价格走势矛盾
    if sm_signal in ("accumulating", "accumulating_confirmed") and price_change < -20:
        penalties.append("⚠️ 筹码建仓但价格下跌背离")
    if sm_signal in ("distributing", "distributing_confirmed") and price_change > 20:
        penalties.append("⚠️ 筹码出货但价格上涨背离")

    total = sum(dims.values())
    grade = "A+" if total >= 80 else ("A" if total >= 65 else ("B" if total >= 50 else ("C" if total >= 35 else "D")))

    direction_tag = determine_direction_tag(
        token_data, volume_anomalies, holder_trend, price_change,
        dims, tech_indicators, smart_money_signal
    )

    return {
        "total_score": total,
        "grade": grade,
        "dimension_scores": dims,
        "penalties": penalties,
        "direction_tag": direction_tag,
        "breakdown": {k: f"{v}/{SCORE_WEIGHTS[k]}" for k, v in dims.items()}
    }


def determine_direction_tag(token_data, volume_anomalies, holder_trend, price_change_raw,
                             dims, tech_indicators, smart_money_signal):
    try:
        price_change = float(price_change_raw) if isinstance(price_change_raw, str) else price_change_raw
    except:
        price_change = 0
    holder_sig, holder_pct = holder_trend
    rsi = tech_indicators.get("rsi")
    sm_sig = smart_money_signal or "unknown"

    # v6 核心逻辑：筹码信号优先
    # 多个信号叠加时确认度更高
    signals = []

    # 筹码建仓信号
    if sm_sig in ("accumulating", "accumulating_confirmed"):
        signals.append(("chip_accum", 3 if sm_sig == "accumulating_confirmed" else 2))

    # 筹码出货信号
    if sm_sig in ("distributing", "distributing_confirmed"):
        signals.append(("chip_dist", 3 if sm_sig == "distributing_confirmed" else 2))

    # RSI 超卖 + 上涨
    if rsi is not None and rsi <= 30 and price_change > 0:
        signals.append(("rsi_oversold", 2))

    # 放量拉升
    if volume_anomalies:
        拉升 = [a for a in volume_anomalies if a["type"] == "放量拉升"]
        下跌 = [a for a in volume_anomalies if a["type"] == "放量下跌"]
        if 拉升 and rsi is not None and rsi <= 40:
            signals.append(("volume_break", 2))
        if 下跌 and 拉升 and dims["市场确认"] >= 8 and dims["资金强度"] >= 12:
            signals.append(("whale_flip", 2))
        if len(下跌) >= 2 and holder_sig != "accumulating":
            signals.append(("distribution", 2))

    # 持币增加
    if holder_sig == "accumulating":
        signals.append(("holder_accum", 1))
    elif holder_sig == "distributing":
        signals.append(("holder_dist", 1))

    # 计算信号强度
    accum_score = sum(s[1] for s in signals if s[0] in ("chip_accum", "rsi_oversold", "volume_break", "whale_flip", "holder_accum"))
    dist_score = sum(s[1] for s in signals if s[0] in ("chip_dist", "distribution", "holder_dist"))

    if not volume_anomalies:
        if rsi is not None:
            if rsi <= 30 and sm_sig in ("accumulating", "accumulating_confirmed") and dims["市场确认"] >= 6:
                return "ACCUMULATION"
            elif rsi >= 70 and sm_sig in ("distributing", "distributing_confirmed") and dims["市场确认"] >= 6:
                return "DISTRIBUTION"
        if sm_sig in ("accumulating", "accumulating_confirmed") and dims["筹码动向"] >= 10:
            return "ACCUMULATION"
        elif sm_sig in ("distributing", "distributing_confirmed") and dims["筹码动向"] <= 0:
            return "DISTRIBUTION"
        if holder_sig == "accumulating" and dims["市场确认"] >= 8:
            return "ACCUMULATION"
        elif holder_sig == "distributing" and dims["市场确认"] >= 8:
            return "DISTRIBUTION"
        return "WATCH_ONLY"

    拉升 = [a for a in volume_anomalies if a["type"] == "放量拉升"]
    对敲 = [a for a in volume_anomalies if a["type"] == "对敲换手"]
    下跌 = [a for a in volume_anomalies if a["type"] == "放量下跌"]

    # v6 筹码信号强化
    if sm_sig in ("accumulating", "accumulating_confirmed") and (拉升 or 对敲) and dims["市场确认"] >= 6:
        return "ACCUMULATION"
    if sm_sig in ("distributing", "distributing_confirmed") and 下跌 and dims["市场确认"] >= 6:
        return "DISTRIBUTION"
    if accum_score >= 4 and dims["筹码动向"] >= 8:
        return "ACCUMULATION"
    if dist_score >= 4 and dims["筹码动向"] <= 2:
        return "DISTRIBUTION"
    if len(拉升) >= 2 and dims["流动性"] >= 5:
        return "BREAKOUT"
    if len(拉升) == 1 and dims["市场确认"] >= 10 and dims["时间优势"] >= 5:
        return "BREAKOUT"
    if len(下跌) >= 2 and holder_sig != "accumulating":
        return "DISTRIBUTION"
    return "WATCH_ONLY"


def format_hermes_alert_v6(token, score_data, volume_anomalies, premium_data, dex_info,
                            tech_indicators, tokenomics, smart_money_flow, timestamp,
                            hftz_data=None):  # v7: + hftz_data
    symbol = token.get("symbol", "UNKNOWN")
    score_val = score_data["total_score"]
    grade = score_data["grade"]
    direction = score_data["direction_tag"]
    price = token.get("lastPrice", dex_info.get("price") if dex_info else "N/A")
    try:
        change = float(token.get("priceChangePercent", 0))
    except:
        change = 0
    try:
        liquidity = float(token.get("liquidity", 0))
    except:
        liquidity = 0
    try:
        holders = int(token.get("holders", 0))
    except:
        holders = 0

    contract = token.get("contractAddress", "")
    chain = token.get("chain", "")
    address = contract or (dex_info.get("address") if dex_info else "")

    risk_info = get_token_risk(chain, address) if address else {"risk_level": "UNKNOWN", "tags": []}
    risk_tags = " ".join(risk_info.get("tags", []))
    rsi = tech_indicators.get("rsi") if tech_indicators else None
    rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
    rsi_sig = ("RSI超卖" if rsi is not None and rsi <= 30 else ("RSI超买" if rsi is not None and rsi >= 70 else ""))
    tok_str = ""
    if tokenomics and tokenomics.get("has_data"):
        upcoming = tokenomics.get("upcoming_30d", 0)
        if upcoming > 0:
            tok_str = f"| 30d解锁:{upcoming:,.0f}"

    holder_sig, _ = detect_holder_signal(symbol, holders) if holders else ("unknown", 0)

    # v6 新增：筹码动向证据
    smf = smart_money_flow or {}
    sm_signal = smf.get("signal", "unknown")
    vc_in = smf.get("vc_in_count", 0)
    vc_out = smf.get("vc_out_count", 0)
    whale_in = smf.get("whale_in_count", 0)
    whale_out = smf.get("whale_out_count", 0)
    ex_in = smf.get("exchange_in_count", 0)
    ex_out = smf.get("exchange_out_count", 0)
    net_exchange = ex_in - ex_out

    evidence = {"链上证据": [], "CEX证据": [], "衍生品证据": [], "技术指标": [], "筹码动向": [], "尚未确认": [], "HFTZ筹码": []}
    if holder_sig != "unknown":
        evidence["链上证据"].append(f"持币趋势: {holder_sig}")
    if dex_info:
        evidence["链上证据"].append(f"Dex: ${dex_info.get('price', 0):.6f} {dex_info.get('change24h', 0):+.1f}%")

    # v6 筹码动向证据
    if sm_signal != "unknown":
        sig_emoji = "🟢" if "accum" in sm_signal else ("🔴" if "dist" in sm_signal else "⚪")
        evidence["筹码动向"].append(f"{sig_emoji} 筹码信号: {sm_signal}")
    if vc_in > 0:
        evidence["筹码动向"].append(f"VC买入: {vc_in}次 | VC卖出: {vc_out}次")
    if whale_in > 0:
        evidence["筹码动向"].append(f"鲸鱼买入: {whale_in}次 | 鲸鱼卖出: {whale_out}次")
    if ex_in > 0:
        evidence["筹码动向"].append(f"→交易所: {ex_in}次 | ←交易所: {ex_out}次")
    if net_exchange != 0:
        arrow = "卖出↑" if net_exchange > 0 else "买入↑"
        evidence["筹码动向"].append(f"交易所净: {arrow} {abs(net_exchange)}次")

    # v6.2 新增：DexScreener 24h buy/sell 数据（Arkham 无数据时的补充）
    dex_buys = smf.get("dex_buy_24h", 0)
    dex_sells = smf.get("dex_sell_24h", 0)
    dex_ratio = smf.get("dex_buy_sell_ratio", 0)
    if dex_buys > 0 or dex_sells > 0:
        ratio_str = f"{dex_ratio:.1f}x" if dex_ratio != float('inf') else "∞"
        arrow_d = "买入↑" if dex_ratio > 1 else "卖出↑"
        evidence["筹码动向"].append(f"Dex 24h: 买{dex_buys}次/卖{dex_sells}次({ratio_str}) {arrow_d}")

    # v7 新增：HertzFlow 筹码三分法报告
    if hftz_data:
        hf = hftz_data
        holders_hf = hf.get("holders", {})
        op_pct = holders_hf.get("operator_pct", 0.0)
        cex_pct = holders_hf.get("cex_pool_pct", 0.0)
        retail_pct = holders_hf.get("verifiable_retail_pct", 0.0)

        hf_signal = hf.get("combined_signal", "none")
        hf_verdict = hf.get("verdict", "WATCH")
        hf_score = hf.get("hftz_score", 0)
        hf_flags = hf.get("risk_flags", [])

        sig_icon = "🟢" if hf_signal == "accumulating" else ("🔴" if hf_signal == "distributing" else "⚪")
        evidence["HFTZ筹码"].append(f"{sig_icon} HFTZ信号: {hf_signal} | 评分: {hf_score}/100 | 判定: {hf_verdict}")
        evidence["HFTZ筹码"].append(f"三分法: Operator {op_pct:.1f}% | CEX Pool {cex_pct:.1f}% | 零售 {retail_pct:.1f}%")

        # Proxy 检测
        proxy_data = hf.get("proxy", {})
        if proxy_data.get("has_proxy"):
            proxy_type = proxy_data.get("proxy_type", "unknown")
            proxy_sig = proxy_data.get("signal", "none")
            evidence["HFTZ筹码"].append(f"Proxy: {proxy_type} | 分发信号: {proxy_sig}")

        # Dumper 分类
        dumper_data = hf.get("dumper", {})
        dumper_cls = dumper_data.get("dumper_class", "none")
        if dumper_cls != "none":
            cex_ratio = dumper_data.get("cex_transfer_ratio", 0.0)
            evidence["HFTZ筹码"].append(f"Dumper: {dumper_cls} | CEX渗透率: {cex_ratio:.0f}%")

        # 风险标注
        if hf_flags:
            for flag in hf_flags:
                evidence["HFTZ筹码"].append(f"⚠️ {flag}")

    evidence["CEX证据"].append(f"Alpha Score: {token.get('score', 0):.0f} | 24h: {change:+.1f}%")
    evidence["CEX证据"].append(f"成交量异动: {'有' if volume_anomalies else '无'}")
    if premium_data:
        evidence["衍生品证据"].append(f"机构溢价: {premium_data.get('premium_pct', 0):+.2f}%")
    if rsi is not None:
        evidence["技术指标"].append(f"RSI: {rsi:.1f} {rsi_sig}")
    if tokenomics and tokenomics.get("has_data"):
        evidence["技术指标"].append(f"解锁: {tokenomics.get('total_unlocks')}事件, 30d内{tokenomics.get('upcoming_30d', 0):,.0f}")
    if not volume_anomalies:
        evidence["尚未确认"].append("成交量异动待观察")
    # 当 Arkham 流数据为空但有 HFTZ 五分法数据时，用 HFTZ 信号补位
    if sm_signal == "unknown" and dex_buys == 0 and dex_sells == 0 and not hftz_data:
        evidence["尚未确认"].append("筹码流数据待补")
    elif sm_signal == "unknown" and (dex_buys > 0 or dex_sells > 0):
        evidence["尚未确认"].append("Dex数据已补(Arkham无覆盖)")

    # HFTZ 五分法数据存在时，用其信号补位"筹码动向"证据槽（覆盖待补）
    if hftz_data:
        hf = hftz_data
        hf_signal = hf.get("combined_signal", "neutral")
        hf_verdict = hf.get("verdict", "WATCH")
        op_pct = hf.get("holders", {}).get("operator_pct", 0.0)
        cex_pct = hf.get("holders", {}).get("cex_pool_pct", 0.0)
        retail_pct = hf.get("holders", {}).get("verifiable_retail_pct", 0.0)
        proxy_data = hf.get("proxy", {})
        dumper_data = hf.get("dumper", {})
        hf_flags = hf.get("risk_flags", [])
        sig_icon = "🟢" if hf_signal == "accumulating" else ("🔴" if hf_signal == "distributing" else "⚪")
        # 覆盖"待补"：HFTZ 五分法即筹码信号
        evidence["筹码动向"] = [
            f"{sig_icon} HFTZ信号: {hf_signal} | 判定: {hf_verdict}",
            f"三分法: Operator {op_pct:.1f}% | CEX Pool {cex_pct:.1f}% | 零售 {retail_pct:.1f}%",
        ]
        if proxy_data.get("has_proxy"):
            evidence["筹码动向"].append(f"Proxy分发: {proxy_data.get('proxy_type','?')} ({proxy_data.get('signal','?')})")
        dumper_cls = dumper_data.get("dumper_class", "none")
        if dumper_cls != "none":
            evidence["筹码动向"].append(f"Dumper: {dumper_cls} | CEX渗透率: {dumper_data.get('cex_transfer_ratio',0):.0f}%")
        for flag in hf_flags:
            evidence["筹码动向"].append(f"⚠️ {flag}")

    liq_penalty = f"⚠️ 流动性仅 ${liquidity/1000:.0f}K" if liquidity < 100_000 else ""

    action = "WATCH_ONLY"
    if grade in ("A+", "A") and direction == "ACCUMULATION":
        action = "PREPARE"
    elif grade in ("A+", "A") and direction == "BREAKOUT":
        action = "SMALL_SIZE"
    elif grade in ("A+", "A") and direction == "DISTRIBUTION":
        action = "EXIT_RISK"

    invalidation = []
    if direction == "ACCUMULATION":
        invalidation = ["跌穿支撑", "Score跌破100", "溢价转负", "筹码背离"]
    elif direction == "BREAKOUT":
        invalidation = ["回踩", "量能萎缩", "筹码信号转空"]
    elif direction == "DISTRIBUTION":
        invalidation = ["价格企稳", "VC再次建仓"]

    dims = score_data["dimension_scores"]

    # v7: HertzFlow verdict 行
    hf_line = ""
    if hftz_data:
        hf_verdict = hftz_data.get("verdict", "WATCH")
        hf_signal = hftz_data.get("combined_signal", "none")
        hf_icon = "🟢" if hf_signal == "accumulating" else ("🔴" if hf_signal == "distributing" else "⚪")
        hf_line = f"{hf_icon} HFTZ: {hf_verdict}"

    output = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【{symbol}】{direction} [{grade} {score_val}/100]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 {price} | {change:+.1f}% | 💧${liquidity/1e6:.2f}M | 👥{holders} | 📈RSI {rsi_str} {tok_str}
|🏷️ {direction} | 📋 {action} {hf_line}
风险: {risk_tags or '无'} {liq_penalty}

【评分】资金{dims['资金强度']}/{SCORE_WEIGHTS['资金强度']} | 地址{dims['地址质量']}/{SCORE_WEIGHTS['地址质量']} | 确认{dims['市场确认']}/{SCORE_WEIGHTS['市场确认']} | 催化{dims['催化权重']}/{SCORE_WEIGHTS['催化权重']} | 流动{dims['流动性']}/{SCORE_WEIGHTS['流动性']} | 时间{dims['时间优势']}/{SCORE_WEIGHTS['时间优势']} | 筹码{dims['筹码动向']}/{SCORE_WEIGHTS['筹码动向']} | 风险{dims['风险结构']}/{SCORE_WEIGHTS['风险结构']}
扣分: {', '.join(score_data['penalties']) or '无'}

【证据】
  链上: {'; '.join(evidence['链上证据']) or '待补'}
  HFTZ: {'; '.join(evidence['HFTZ筹码']) or '无HFTZ数据'}
  筹码: {'; '.join(evidence['筹码动向']) or '待补'}
  CEX: {'; '.join(evidence['CEX证据']) or '待补'}
  衍生: {'; '.join(evidence['衍生品证据']) or '待补'}
  技术: {'; '.join(evidence['技术指标']) or '待补'}

【失效】{', '.join(invalidation) or '需补充'}"""
    if volume_anomalies:
        for a in volume_anomalies:
            e = "📈" if a["type"] == "放量拉升" else ("📉" if a["type"] == "放量下跌" else "↔️")
            output += f"\n  {e} {a['date']} {a['volume_ratio']:.1f}x {a['price_change']:+.1f}%"
    else:
        output += "\n  （近7天无异常成交量）"

    # v6 新增：Top 流入/流出钱包
    # top_in_wallets: [(label_or_addr, "in"/"out", count)]
    top_in = smf.get("top_in_wallets", [])
    top_out = smf.get("top_out_wallets", [])
    if top_in:
        output += "\n🟢 Top 买入 (Arkham/Dex):"
        for item in top_in[:3]:
            if isinstance(item, tuple) and len(item) >= 3:
                label, ltype, cnt = item[0], item[1], item[2]
                output += f"\n  {label} ({ltype}) {cnt}次"
            else:
                output += f"\n  {item}"
    if top_out:
        output += "\n🔴 Top 卖出 (Arkham/Dex):"
        for item in top_out[:3]:
            if isinstance(item, tuple) and len(item) >= 3:
                label, ltype, cnt = item[0], item[1], item[2]
                output += f"\n  {label} ({ltype}) {cnt}次"
            else:
                output += f"\n  {item}"

    return output


# ====== 主扫描 v6 ======

def process_token_baseline(token, premium, fear_greed):
    """处理单个代币的基线数据（无 surf 调用）"""
    symbol = token.get("symbol", "")
    if not symbol:
        return None

    address = token.get("contractAddress", "")
    try:
        change = float(token.get("priceChangePercent", 0))
    except:
        change = 0
    try:
        score = float(token.get("score", 0))
    except:
        score = 0

    holder_trend = detect_holder_signal(symbol, token.get("holders", 0))
    vol_anomaly = get_volume_anomaly(symbol)
    dex_info = get_dex_price(address) if address else None

    rough_score = calculate_signal_score(
        token, vol_anomaly, premium, holder_trend,
        {"rsi": None}, {"has_data": False}, fear_greed,
        {}, "unknown"
    )

    return {
        "token": token, "symbol": symbol, "address": address,
        "change": change, "score": score,
        "holder_trend": holder_trend, "vol_anomaly": vol_anomaly,
        "dex_info": dex_info, "rough_score": rough_score
    }


def enhance_batch_v6(batch_results, premium, fear_greed):
    """对一批候选进行 surf Arkham 精细增强（batch 内 token 并行）"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def process_one(r):
        symbol = r["symbol"]
        token = r["token"]
        address = r["address"]
        chain = token.get("chain", "ethereum")

        tech = {"rsi": None}
        tokenomics = {"has_data": False}
        smart_flow = {}
        smart_signal = "unknown"

        score_f = float(token.get("score", 0))
        change_f = abs(r["change"])
        rough = r["rough_score"]["total_score"]

        do_flow = r.get("_do_flow", False)
        do_hftz = r.get("_do_hftz", False)  # v7: HertzFlow 全量分析（独立于 do_flow）

        # v7: HertzFlow 完整筹码分析（与 do_flow 并行，独立执行）
        if do_hftz:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fhftz = ex.submit(hftz_run_chip_analysis, address, chain)
                try:
                    hftz_result = fhftz.result(timeout=55)
                    r["hftz_data"] = hftz_result
                except Exception:
                    r["hftz_data"] = None
        else:
            r["hftz_data"] = None

        if do_flow:
            with ThreadPoolExecutor(max_workers=4) as ex:  # v7: 4 workers（+ HertzFlow）
                ft = ex.submit(get_token_tech_indicators, symbol)
                fto = ex.submit(get_token_tokenomics, symbol)
                fflow = ex.submit(get_token_transfer_flow, address, chain, 15)
                try:
                    tech = ft.result(timeout=15) or {"rsi": None}
                except:
                    tech = {"rsi": None}
                try:
                    tokenomics = fto.result(timeout=15) or {"has_data": False}
                except:
                    tokenomics = {"has_data": False}
                try:
                    flow_result = fflow.result(timeout=40)
                    if flow_result:
                        smart_flow = flow_result
                        smart_signal = analyze_smart_money_historical(symbol, smart_flow)
                except:
                    pass
        else:
            with ThreadPoolExecutor(max_workers=2) as ex:
                ft = ex.submit(get_token_tech_indicators, symbol)
                fto = ex.submit(get_token_tokenomics, symbol)
                try:
                    tech = ft.result(timeout=12) or {"rsi": None}
                except:
                    tech = {"rsi": None}
                try:
                    tokenomics = fto.result(timeout=12) or {"has_data": False}
                except:
                    tokenomics = {"has_data": False}

        final_score = calculate_signal_score(
            token, r["vol_anomaly"], premium, r["holder_trend"],
            tech, tokenomics, fear_greed,
            smart_flow, smart_signal
        )

        # ====== v7 HertzFlow 评分后处理 ======
        # 在 calculate_signal_score 基础上，追加 HertzFlow 筹码三分法权重
        hftz_data = r.get("hftz_data")
        if hftz_data and isinstance(hftz_data, dict):
            hftz = hftz_data
            hftz_signal = hftz.get("combined_signal", "neutral")
            hftz_verdict = hftz.get("verdict", "WATCH")
            hftz_score = hftz.get("hftz_score", 0)
            # holders 字段：hftz_get_token_holders 的返回结果（含 operator_pct 等）
            holders_data = hftz.get("holders") or {}
            operator_pct = holders_data.get("operator_pct", 0)
            cex_pct = holders_data.get("cex_pool_pct", 0)
            retail_pct = holders_data.get("verifiable_retail_pct", 0)
            chip_risk_flags = hftz.get("risk_flags", [])

            # 1. HertzFlow 筹码动向维度 (0-15)，替换原有的 neutral 值
            if hftz_signal == "accumulating":
                hftz_chip = 14
            elif hftz_signal == "distributing":
                hftz_chip = -5
            elif hftz_signal == "quiet_distribution":
                hftz_chip = -3
            else:  # neutral
                hftz_chip = 5  # 无明显信号，中性给分

            # 2. Operator 持仓过高 → 扣分（超过50%严重扣减）
            if operator_pct > 60:
                hftz_chip = max(-5, hftz_chip - 8)
                if "Operator持仓过高(>60%)" not in final_score["penalties"]:
                    final_score["penalties"].append(f"Operator持仓过高({operator_pct:.0f}%)")
            elif operator_pct > 40:
                hftz_chip = max(-3, hftz_chip - 4)
                if "Operator持仓偏高" not in final_score["penalties"]:
                    final_score["penalties"].append(f"Operator持仓偏高({operator_pct:.0f}%)")
            elif operator_pct > 25:
                hftz_chip = max(0, hftz_chip - 1)  # 轻微影响

            final_score["dimension_scores"]["筹码动向"] = max(-5, min(15, hftz_chip))

            # 3. CEX 渗透率过高 → 风险结构降分
            if cex_pct > 50:
                final_score["dimension_scores"]["风险结构"] = max(0, final_score["dimension_scores"]["风险结构"] - 4)
                if "CEX持仓渗透过高" not in final_score["penalties"]:
                    final_score["penalties"].append(f"CEX持仓过高({cex_pct:.0f}%)")
            elif cex_pct > 35:
                final_score["dimension_scores"]["风险结构"] = max(0, final_score["dimension_scores"]["风险结构"] - 2)

            # 4. Dumper verdict → 调整方向标签
            if hftz_verdict in ("EXIT_ALL", "FULL_DUMP"):
                final_score["direction_tag"] = "DISTRIBUTION"
                final_score["penalties"].append("HFTZ: 全量出货中")
            elif hftz_verdict == "REDUCE":
                if final_score["direction_tag"] not in ("DISTRIBUTION",):
                    final_score["direction_tag"] = "DISTRIBUTION"

            # 5. 重新计算总分
            dims = final_score["dimension_scores"]
            final_score["total_score"] = sum(dims.values())
            final_score["grade"] = (
                "A+" if final_score["total_score"] >= 80 else
                "A" if final_score["total_score"] >= 65 else
                "B" if final_score["total_score"] >= 50 else
                "C" if final_score["total_score"] >= 35 else "D"
            )
            final_score["breakdown"] = {k: f"{v}/{SCORE_WEIGHTS[k]}" for k, v in dims.items()}

            # 6. 补充 evidence（HFTZ 筹码证据）
            if "evidence" not in final_score:
                final_score["evidence"] = {"HFTZ筹码": []}
            hf_ev = final_score["evidence"].get("HFTZ筹码", [])
            if chip_risk_flags:
                hf_ev.extend(chip_risk_flags)
            if operator_pct > 0:
                hf_ev.append(f"Operator {operator_pct:.0f}% / CEX {cex_pct:.0f}%")
            if hftz.get("dumper_class") and hftz.get("dumper_class") != "none":
                hf_ev.append(f"Dumper: {hftz.get('dumper_class')} ({hftz.get('cex_transfer_ratio',0):.0f}%→CEX)")
            if hftz.get("has_proxy"):
                hf_ev.append(f"Proxy分发: {hftz.get('proxy_type','?')} ({hftz.get('signal','?')})")
            final_score["evidence"]["HFTZ筹码"] = hf_ev

        r["final_score"] = final_score
        r["tech"] = tech
        r["tokenomics"] = tokenomics
        r["smart_money_flow"] = smart_flow
        r["smart_money_signal"] = smart_signal
        return symbol, r

    # batch 内并行（每个 token 独立）
    results_map = {}
    with ThreadPoolExecutor(max_workers=min(len(batch_results), 5)) as ex:
        futures = {ex.submit(process_one, r): r for r in batch_results}
        for future in as_completed(futures):
            try:
                sym, result = future.result(timeout=80)  # v7: 放宽超时（ HertzFlow 最长55s）
                results_map[sym] = result  # ← 原来漏了这行！
            except Exception as e:
                import traceback
                symbol = futures[future] if isinstance(futures[future], str) else futures[future].get("symbol","?")
                print(f"  [WARN] {symbol} enhance failed: {e}")
                traceback.print_exc()
                results_map[symbol] = {"symbol": symbol}

    return results_map


def scan():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 Hermes Radar v6 (Arkham增强) 扫描启动...")

    # ===== Phase 1: 市场情绪 + 溢价 =====
    fear_greed = get_market_fear_greed()
    print(f"[情绪] Fear & Greed: {fear_greed['value']}/100 ({fear_greed['signal']})")

    premium = get_coinbase_premium()
    if premium:
        print(f"[溢价] Binance ${premium['binance_btc']:,.0f} vs Kraken ${premium['kraken_btc']:,.0f} → {premium['premium_pct']:+.2f}%")

    # ===== Phase 2: Alpha 列表 + 市场异动榜 =====
    alpha_list = get_alpha_list()
    print(f"[Alpha] 获取 {len(alpha_list)} 个代币")

    valid_tokens = []
    for t in alpha_list:
        try:
            score_f = float(t.get("score", 0))
            liq_f = float(t.get("liquidity", 0))
            holders_i = int(t.get("holders", 0))
            if score_f >= MIN_SCORE and liq_f >= MIN_LIQUIDITY and holders_i >= MIN_HOLDERS:
                valid_tokens.append(t)
        except:
            continue

    print(f"[过滤] {len(valid_tokens)} 个候选代币")

    top_movers = get_market_top_movers()

    # ===== Phase 3: 并发基线处理（无 surf 调用，快速）=====
    print(f"[基线] 并发处理 {len(valid_tokens)} 个代币...")
    baseline_results = []
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {
            executor.submit(process_token_baseline, t, premium, fear_greed): t
            for t in valid_tokens
        }
        done_count = 0
        for future in as_completed(futures):
            result = future.result()
            if result:
                baseline_results.append(result)
            done_count += 1
            if done_count % 20 == 0:
                print(f"[进度] {done_count}/{len(valid_tokens)}")

    print(f"[基线] 完成 {len(baseline_results)} 个")

    # ===== Phase 4: surf 精细增强 =====
    # 所有候选都做 RSI + tokenomics（全量，batch 并行）
    # Arkham 筹码流对三类候选执行（扩大覆盖）：
    #   - Score=111（最高优先级）→ HertzFlow 全量分析
    #   - Score≥108（次优先级）→ Arkham flow
    #   - |24h涨幅|≥15%（异动币）→ Arkham flow

    sorted_by_score = sorted(baseline_results, key=lambda r: (
        -float(r["token"].get("score", 0)),
        -abs(r["change"]),
        -r["rough_score"]["total_score"]
    ))

    # v7 HertzFlow 候选：仅限 Score=111（最高优先级，最多5个）
    hftz_tokens = [
        r for r in sorted_by_score
        if float(r["token"].get("score", 0)) >= 111
    ][:5]
    hftz_symbols = {r["symbol"] for r in hftz_tokens}
    print(f"[HFTZ] {len(hftz_symbols)} 个代币进入 HertzFlow 完整筹码分析(Score=111)")
    if hftz_symbols:
        print(f"       符号: {sorted(hftz_symbols)}")

    # Arkham flow 候选：Score≥50(B+) 或 |change|≥15% 或 Score=111（HFTZ已有）
    # 原阈值108太高，导致B+候选(MET/XPL/BANANAS31)跳过了 tech+tokenomics 抓取
    flow_tokens = [
        r for r in sorted_by_score
        if float(r["token"].get("score", 0)) >= 50   # 包含所有B+及以上
        or abs(r["change"]) >= 15
    ][:30]
    flow_symbols = {r["symbol"] for r in flow_tokens} - hftz_symbols
    print(f"[surf] {len(flow_symbols)} 个候选进入 Arkham 筹码扫描(Score≥50或|change|≥15%)")

    # 所有候选分批做 RSI + tokenomics（batch 并行，每个 batch 最多 5 个 token）
    BATCH_SIZE = 5
    enhanced_all = {}
    all_candidates = baseline_results  # 全量

    print(f"[surf] 开始精细增强（全量 {len(all_candidates)} 个候选，{len(hftz_symbols)} 个 HertzFlow + {len(flow_symbols)} 个 Arkham）...")
    for i in range(0, len(all_candidates), BATCH_SIZE):
        batch = all_candidates[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(all_candidates) + BATCH_SIZE - 1) // BATCH_SIZE
        # 标记哪些需要跑 Arkham flow 和 HertzFlow
        for r in batch:
            r["_do_flow"] = r["symbol"] in flow_symbols
            r["_do_hftz"] = r["symbol"] in hftz_symbols  # v7: HertzFlow
        print(f"[surf] 批次 {batch_num}/{total_batches} ({len(batch)} 个)...")
        batch_enhanced = enhance_batch_v6(batch, premium, fear_greed)
        enhanced_all.update(batch_enhanced)
        print(f"[surf] 批次 {batch_num} 完成，累计 {len(enhanced_all)} 个")

    print(f"[surf] 完成 {len(enhanced_all)} 个精细增强")

    # ===== Phase 5: 汇总警报 ======
    timestamp = int(datetime.now().timestamp())
    alerts = []
    candidate_tokens = []

    for r in baseline_results:
        symbol = r["symbol"]
        token = r["token"]
        final_score = enhanced_all.get(symbol, {}).get("final_score", r["rough_score"])
        tech = enhanced_all.get(symbol, {}).get("tech", {"rsi": None})
        tokenomics = enhanced_all.get(symbol, {}).get("tokenomics", {"has_data": False})
        dex_info = r["dex_info"]
        smart_money_flow = enhanced_all.get(symbol, {}).get("smart_money_flow", {})
        smart_money_signal = enhanced_all.get(symbol, {}).get("smart_money_signal", "unknown")
        hftz_data = enhanced_all.get(symbol, {}).get("hftz_data", None)  # v7: HertzFlow

        price_at_signal = token.get("lastPrice", dex_info.get("price") if dex_info else 0)
        try:
            price_at_signal = float(price_at_signal)
        except:
            price_at_signal = 0

        if final_score["grade"] in ("A+", "A"):
            update_replay(symbol, final_score["direction_tag"], final_score["total_score"], price_at_signal, timestamp, token=token)

        if final_score["grade"] in ("A+", "A", "B") and final_score["direction_tag"] != "WATCH_ONLY":
            alert_text = format_hermes_alert_v6(
                token, final_score, r["vol_anomaly"], premium, dex_info,
                tech, tokenomics, smart_money_flow, timestamp, hftz_data  # v7: + hftz_data
            )
            hftz_signal = hftz_data.get("combined_signal", "none") if hftz_data else "none"
            hftz_verdict = hftz_data.get("verdict", "WATCH") if hftz_data else "WATCH"
            alerts.append({
                "symbol": symbol, "grade": final_score["grade"],
                "score": final_score["total_score"],
                "direction": final_score["direction_tag"], "text": alert_text,
                "smart_money_signal": smart_money_signal,
                "hftz_signal": hftz_signal,  # v7: HertzFlow筹码信号
                "hftz_verdict": hftz_verdict,  # v7: HertzFlow判决
                "hftz_score": hftz_data.get("hftz_score", 0) if hftz_data else 0,
            })

        candidate_tokens.append((symbol, r["change"], r["score"], final_score))

    # 分类结果
    results = {"accumulation_early": [], "overheated": [], "distribution": []}
    for sym, change, score, sr in candidate_tokens:
        if -GAIN_ACCUMULATION_EARLY <= change <= GAIN_ACCUMULATION_EARLY and score >= MIN_SCORE:
            results["accumulation_early"].append((sym, score, change))
        elif change > GAIN_OVERHEATED:
            results["overheated"].append((sym, change, score))
        elif change < DUMP_THRESHOLD:
            results["distribution"].append((sym, change, score))

    generate_report_v6(premium, results, alpha_list, alerts, fear_greed, top_movers, timestamp)
    print(f"[完成] {len(alerts)} 条 B+ 级警报")


def generate_report_v6(premium, results, alpha_list, alerts, fear_greed, top_movers, timestamp):
    accumulation_early = sorted(results["accumulation_early"], key=lambda x: -x[1])[:20]
    gainers = top_movers.get("gainers", [])
    losers = top_movers.get("losers", [])

    # 筹码动向统计
    accum_signals = [a for a in alerts if "accum" in a.get("smart_money_signal", "")]
    dist_signals = [a for a in alerts if "dist" in a.get("smart_money_signal", "")]

    report = f"""# 🚨 Hermes Radar — Binance Alpha 每日扫描 (Arkham增强)
**扫描时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')} |
**版本**: v6 (Arkham/Surf 筹码动向) |
**数据源**: Binance Alpha API + DexScreener + surf (wallet-transfers/Arkham labels)

---

## 📊 市场温度

| 指标 | 数值 | 信号 |
|------|------|------|
| BTC Binance | ${premium['binance_btc']:,.0f} | — |
| BTC Kraken | ${premium['kraken_btc']:,.0f} | — |
| 机构溢价 | **{premium['premium_pct']:+.2f}%** | {premium['signal']} |
| **Fear & Greed** | **{fear_greed['value']}/100** | {fear_greed['signal']} |
| Alpha 总数 | {len(alpha_list)} | — |
| Score≥100 | {len([t for t in alpha_list if float(t.get('score', 0)) >= 100])} | — |
| 🟢 筹码建仓信号 | {len(accum_signals)} 个 | — |
| 🔴 筹码出货信号 | {len(dist_signals)} 个 | — |

---

## 🎯 B级以上警报（共{len(alerts)}条，含Arkham筹码信号）

"""
    if alerts:
        sorted_alerts = sorted(alerts, key=lambda x: -x["score"])
        for i, alert in enumerate(sorted_alerts[:10], 1):
            sm_sig = alert.get("smart_money_signal", "unknown")
            sm_icon = "🟢" if "accum" in sm_sig else ("🔴" if "dist" in sm_sig else "⚪")
            report += f"### {i}. {alert['symbol']} — {alert['direction']} [{alert['grade']} {alert['score']}/100] {sm_icon}\n"
            lines = alert['text'].split('\n')
            for line in lines:
                # 保留：标题行 + 证据内容行（含 "  链上:" "  筹码:" 缩进行）
                is_title = any(k in line for k in ["【", "💰", "🏷️", "【评分】", "【证据】", "【失效】", "📈", "📉", "↔️"])
                is_evidence_content = line.startswith("  ") and any(k in line for k in ["链上:", "筹码:", "CEX:", "衍生:", "技术:"])
                is_top_wallet = "🟢 Top" in line or "🔴 Top" in line
                if is_title or is_evidence_content or is_top_wallet:
                    report += line + "\n"
            report += "\n---\n"
    else:
        report += "_暂无 B+ 级警报_\n\n"

    report += f"""## 📈📉 市场异动榜（surf market-ranking）

### 🔼 Top Gainers
| 代币 | 价格 | 24h涨跌 | 市值 |
|------|------|---------|------|
"""
    for g in gainers[:10]:
        try:
            ch = float(g.get("change_24h_pct", 0))
            arrow = "🔼" if ch > 0 else "🔽"
            report += f"| {g.get('name', g.get('symbol','N/A'))} | ${float(g.get('price_usd',0)):.4f} | {arrow}{abs(ch):.1f}% | ${float(g.get('market_cap_usd',0))/1e6:.1f}M |\n"
        except:
            report += f"| {g.get('symbol','N/A')} | — | — | — |\n"

    report += """
### 🔽 Top Losers
| 代币 | 价格 | 24h涨跌 | 市值 |
|------|------|---------|------|
"""
    for l in losers[:10]:
        try:
            ch = float(l.get("change_24h_pct", 0))
            arrow = "🔼" if ch > 0 else "🔽"
            report += f"| {l.get('name', l.get('symbol','N/A'))} | ${float(l.get('price_usd',0)):.4f} | {arrow}{abs(ch):.1f}% | ${float(l.get('market_cap_usd',0))/1e6:.1f}M |\n"
        except:
            report += f"| {l.get('symbol','N/A')} | — | — | — |\n"

    report += f"""
---

## 📋 吸筹前期（Score≥100, 变化±10%）— 共{len(accumulation_early)}个

| 代币 | Score | 24h变化 |
|------|-------|---------|
"""
    for sym, score, change in accumulation_early[:15]:
        report += f"| {sym} | {score:.0f} | {change:+.1f}% |\n"

    report += """
---

## 📖 评级说明

| 等级 | 分数 | 动作 |
|------|------|------|
| A+ | 80+ | PREPARE |
| A | 65-79 | 重点跟踪 |
| B | 50-64 | 等待确认 |

## 🏷️ 方向标签

| 标签 | 含义 |
|------|------|
| ACCUMULATION | 多信号共振：筹码建仓 + RSI/量能确认 |
| BREAKOUT | 放量拉升突破 |
| DISTRIBUTION | 多信号共振：筹码出货 + 放量下跌 |
| WATCH_ONLY | 信息不足 |

## 🔍 v6 新增：Arkham筹码信号说明

| 信号 | 含义 |
|------|------|
| accumulating_confirmed | 连续2次检测到 VC/鲸鱼 建仓信号 |
| accumulating | 检测到 VC(≥2次) 或 鲸鱼(≥3次) 买入 |
| distributing_confirmed | 连续2次检测到交易所净流入抛压 |
| distributing | 交易所大量净流入 且无 VC/鲸鱼 承接 |
| neutral | 无明显方向 |

---

*Hermes Radar v6 (Arkham Enhanced) — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""

    with open(OUTPUT_FILE, "w") as f:
        f.write(report)
    print(f"[报告] 已写入: {OUTPUT_FILE}")


if __name__ == "__main__":
    scan()