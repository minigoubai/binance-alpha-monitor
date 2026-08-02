# Binance Alpha Monitor
# Binance Alpha 筹码监控

> English below / 中文往下

---

## Overview | 概览

**Binance Alpha Monitor** is an automated token monitoring and chip analysis framework for Binance Alpha tokens. It combines HertzFlow's 5-factor methodology with Surf free tier and Arkham intelligence to track holder distribution, detect smart money flows, and deliver alerts via Telegram.

**Binance Alpha 筹码监控** 是一套全自动的 Binance Alpha 代币监控系统。融合 HertzFlow 五分法筹码分析、Surf 免费链上数据和 Arkham 情报追踪，持仓分布、聪明钱流向、警报推送一体化。

---

## 🏆 Historical Performance | 历史战绩

| 代币 Token | 评级 Tier | 信号 Signal | 信号日 Date | 信号价 Price | 信号后最高 High | **涨幅 Gain** |
|------------|-----------|------------|-------------|--------------|----------------|--------------|
| **BANK** | A+ | 吸筹 ACCUMULATION | 2025-06-09 | $0.0433 | $0.3797 | **🔴 +777%** |
| **SENT** | A | 突破 BREAKOUT | 2026-01-22 | $0.0299 | $0.0480 | **🔴 +60.5%** |
| **AERO** | A | 派发 DISTRIBUTION | 2026-07-17 | $0.3440 | $0.5180 | **🔴 +50.6%** |
| **BANANAS31** | A+ | 突破 BREAKOUT | 2025-06-15 | $0.0108 | $0.0148 | **🟡 +37.0%** |
| **ZBT** | A | 突破 BREAKOUT | 2025-07-02 | $0.1330 | $0.1584 | **🟠 +19.1%** |
| VIRTUAL | A | 派发 DISTRIBUTION | 2025-06-09 | $2.0123 | — | ⚪ 信号滞后（最高在信号前） |
| BEAT/WOD/TRADOOR/RIVER/DN/Fartcoin/Q | A/A+ | 多方向 Various | 2025–2026 | — | — | ⚪ 无Binance现货 |

> 数据来源：`alpha_signal_replay.json`（6,189条信号）截至2026-08-02
> Source: `alpha_signal_replay.json` (6,189 signals) as of 2026-08-02

**信号方向 vs 实际效果 | Signal Direction vs Reality:**
- **ACCUMULATION（吸筹）** → 🟢 最强买入信号（BANK +777%）
- **BREAKOUT（突破）** → 🟢 高胜率信号（SENT +60.5%、AERO +50.6%）
- **DISTRIBUTION（派发）** → ⚠️ 不可做空！AERO在DISTRIBUTION信号后仍+50.6%

---

## Features | 功能

| 功能 Feature | 说明 Description |
|------------|----------------|
| **Alpha 扫描 Alpha Scanner** | 每4小时自动扫描650+代币，按 Score/流动性过滤 Scans 650+ tokens every 4h, filters by Score/liquidity |
| **筹码分析 Chip Analysis** | Operator/CEX Pool/Verifiable Retail 五分法分类 Operator / CEX Pool / Verifiable Retail classification |
| **聪明钱追踪 Smart Money Tracking** | Arkham + Surf 链上转账追溯 Arkham + Surf on-chain flow analysis |
| **Telegram 警报 Telegram Alerts** | A+/A/B 分级警报 + 筹码信号 A+ / A / B tier alerts with chip signals |
| **每日复盘 Daily Review** | 追踪警报后价格走势，自动发布到 Binance Square Tracks post-alert price action, publishes to Binance Square |

---

## Quick Start | 快速开始

### 1. Install Dependencies | 安装依赖

```bash
surf install
surf sync
```

### 2. Configure Telegram Bot | 配置 Telegram Bot

```bash
# Create token file
echo "YOUR_BOT_TOKEN" > ~/.hermes/scripts/telegram_token.txt
```

### 3. Run Scanner | 运行扫描

```bash
cd ~/.hermes/scripts
PYTHONUNBUFFERED=1 python3 binance_alpha_scanner_v6_arkham.py
```

### 4. Set Up Cron Jobs | 配置定时任务

```bash
# Scan every 4 hours
hermes cron add --name "Binance Alpha Scan" \
  --schedule "0 */4 * * *" \
  --repeat forever \
  --deliver "telegram:YOUR_CHAT_ID" \
  --script binance_alpha_scanner_v6_arkham.py

# Auto review after each scan
hermes cron add --name "Alpha Review Loop" \
  --schedule "5 */4 * * *" \
  --repeat forever \
  --script alpha_review_loop.py
```

---

## Directory Structure | 目录结构

```
binance-alpha-monitor/
├── SKILL.md                              # Method guide (EN/CN)
├── scanner/
│   ├── binance_alpha_scanner_v6_arkham.py   # Main scanner
│   ├── alpha_review_loop.py                   # Post-scan review
│   ├── alpha_square_post_v2.py               # Binance Square poster
│   └── alpha_params.json                     # Scan parameters
├── references/
│   ├── alpha-historical-alerts.md           # Historical performance (full report)
│   ├── binance-alpha-scanner-v6-bugs.md     # Bug log
│   ├── telegram-push-pattern.md              # Telegram setup
│   ├── alpha-review-pipeline.md             # Review pipeline
│   └── wash-trading-detection.py            # Wash trading detector
└── outputs/                                 # Auto-generated
    ├── alpha_scan_output.md
    ├── alpha_combined_report.md
    └── alpha_review_loop_report.json
```

---

## Rating System | 评分系统

| Tier | Score | Action | 等级 | 分数 | 动作 |
|------|-------|--------|------|------|------|
| **A+** | 80+ | 🔴 PREPARE — Immediate attention | **A+** | 80+ | 🔴 立即关注 |
| **A** | 65–79 | 🟠 Track — Watch closely | **A** | 65–79 | 🟠 重点跟踪 |
| **B** | 50–64 | 🟡 Wait — Confirm first | **B** | 50–64 | 🟡 等待确认 |

---

## Chip Signals | 筹码信号

| Signal | Meaning | 信号 | 含义 |
|--------|---------|------|------|
| 🟢 Accumulation | VC / Whale buying + CEX net outflow | 🟢 吸筹 | VC/鲸鱼买入 + CEX净流出 |
| 🔴 Distribution | CEX net inflow + no VC support | 🔴 派发 | CEX净流入 + 无VC承接 |
| ⚪ Neutral | Insufficient data or mixed signals | ⚪ 中性 | 数据不足或信号模糊 |

---

## Chip Lifecycle Stages | 筹码生命周期阶段

| Stage | Logic | 阶段 | 逻辑 |
|-------|-------|------|------|
| 🟢 Accumulating | DEX net inflow + CEX% drop + unknown% drop | 🟢 吸筹中 | DEX净流入 + CEX%下降 + 未知地址%下降 |
| 🔴 Distributing | DEX net outflow + CEX% rise + unknown% rise | 🔴 派发中 | DEX净流出 + CEX%上升 + 未知地址%上升 |
| 🔵 Pumping | Price up + volume surge + chips not distributed | 🔵 拉升中 | 价格大涨 + 成交量放大 + 筹码未明显分散 |
| ⚪ Consolidating | No significant change across metrics | ⚪ 横盘整理 | 各指标无明显变化 |
| 🟡 Warning | CEX% or unknown% spike suddenly | 🟡 预警 | CEX%或未知地址%突然大增 |

---

## Data Sources | 数据来源

| Tier | Tool | Coverage | 数据层 | 工具 | 覆盖范围 |
|------|------|----------|--------|------|----------|
| Free | Surf `token-holders` / `token-transfers` | 90% of analysis | 免费 | Surf `token-holders` / `token-transfers` | 90% 分析需求 |
| Free | `onchain-sql` | Mint event tracing | 免费 | `onchain-sql` | Mint 事件追溯 |
| Paid | HertzFlow forensic | Full Rule 11 trace, CEX tier | 付费 | HertzFlow forensic | 完整Rule 11追溯，CEX分层 |

---

## ⚠️ Key Traps | 关键陷阱

### 1. Two Score Systems Exist | 两套评分系统并存

The Alpha API returns a **thousand-point score** (0–111+). The A+/A/B tier uses a **separate internal 0–100 score**. These are NOT the same.

Alpha API 返回**千分制**分数 (0–111+)，A+/A/B 分级使用**另一套内部百分制**。两者完全不同。

```
Alpha API score (thousand-point) → filter candidate pool
    ↓
Internal 0–100 score → A+/A/B tier
    ↓
A+ requires: internal score ≥ 80 + multi-signal confluence
```

### 2. Pre-listing Trap | 预上市陷阱

Alpha API data may show holders / liquidity BEFORE the token actually deploys on-chain. Always verify with `eth_getCode` on the contract address.

Alpha API 可能在代币实际上链前就显示 holders / 流动性数据。用 `eth_getCode` 验证合约是否真正部署。

### 3. Wash Trading Detection | 洗售交易检测

Many early Alpha tokens use wash trading. Look for: proxy contracts bouncing tiny amounts back and forth between each other.

早期 Alpha 代币常见洗售交易。特征：代理合约之间互相来回转移极小额代币。

---

## Disclaimer | 免责声明

This tool is for educational and research purposes only. It does NOT constitute investment advice. Cryptocurrency trading involves substantial risk — DYOR.

本工具仅供学习和研究使用，不构成投资建议。加密货币投资有风险，请DYOR。

---

## License | 许可证

MIT License
