# Binance Alpha Monitor

Binance Alpha 代币自动扫描 + 筹码分析 + Telegram 推送。

## 功能

- **Alpha 扫描**：每4小时自动扫描 Binance Alpha 650+ 代币，按 Score/流动性过滤
- **筹码分析**：融合 HertzFlow 五分法（Operator/CEX Pool/Verifiable Retail） + Arkham/Surf 链上流
- **Telegram 推送**：扫描结果自动推送到指定 Telegram 频道/群组
- **每日复盘**：追踪扫描后价格变化，自动生成复盘报告并发布到 Binance Square

## 快速开始

### 1. 安装依赖

```bash
surf install
surf sync
```

### 2. 配置 Telegram Bot Token

在 `~/.hermes/scripts/` 目录下创建 `telegram_token.txt`，写入你的 Bot Token。

### 3. 运行扫描

```bash
cd ~/.hermes/scripts
PYTHONUNBUFFERED=1 python3 binance_alpha_scanner_v6_arkham.py
```

### 4. 配置定时任务（Cron）

```bash
# 每4小时扫描一次
hermes cron add --name "Binance Alpha 每4小时扫描" \
  --schedule "0 */4 * * *" \
  --repeat forever \
  --deliver "telegram:你的chat_id" \
  --script binance_alpha_scanner_v6_arkham.py

# 扫描完成后自动复盘
hermes cron add --name "Alpha 扫描复盘闭环" \
  --schedule "5 */4 * * *" \
  --repeat forever \
  --script alpha_review_loop.py
```

## 目录结构

```
binance-alpha-monitor/
├── SKILL.md                          # 方法论文档（你正在读这个）
├── scanner/
│   ├── binance_alpha_scanner_v6_arkham.py   # 主扫描脚本
│   ├── alpha_review_loop.py                   # 复盘闭环
│   ├── alpha_square_post_v2.py               # Binance Square 发帖
│   └── alpha_params.json                     # 扫描参数配置
├── references/
│   ├── binance-alpha-scanner-v6-bugs.md      # 已知 Bug 记录
│   ├── telegram-push-pattern.md              # Telegram 推送模式
│   ├── alpha-review-pipeline.md              # 复盘流程文档
│   └── wash-trading-detection.py             # Wash Trading 检测
└── outputs/                         # 扫描输出（自动生成）
    ├── alpha_scan_output.md
    ├── alpha_combined_report.md
    └── alpha_review_loop_report.json
```

## 评分系统

| 等级 | 分数 | 动作 |
|------|------|------|
| A+ | 80+ | 🔴 PREPARE — 立即关注 |
| A | 65-79 | 🟠 重点跟踪 |
| B | 50-64 | 🟡 等待确认 |

## 筹码信号

| 信号 | 含义 |
|------|------|
| 🟢 建仓 | VC/鲸鱼买入 + CEX 净流出 |
| 🔴 出货 | CEX 净流入 + 无 VC 承接 |
| ⚪ 中性 | 数据不足或信号模糊 |

## 免责声明

本工具仅供学习和研究使用，不构成投资建议。加密货币投资有风险，请DYOR。
