# Alpha 扫描复盘闭环框架

## 概述

每次扫描后自动执行：对比市场涨跌幅榜 → 分析漏扫原因 → 自动调参 → 信号暴涨则发推。

## 脚本

- `/home/ubuntu/.hermes/scripts/alpha_review_loop.py` — 复盘闭环脚本
- `/home/ubuntu/.hermes/scripts/alpha_surge_tweets.json` — 暴涨代币推文内容（供发帖脚本读取）

## Cron 调度

| Job ID | 名称 | 调度 | 脚本 |
|--------|------|------|------|
| a73bfe2490c4 | Binance Alpha 每日扫描 v6 | `0 */4 * * *`（每天6次） | binance_alpha_scanner_v6_arkham.py |
| 8b02db475ac6 | Alpha 扫描复盘闭环 | `5 */4 * * *`（扫描后5分钟） | alpha_review_loop.py |
| 13af9a938add | Alpha 广场发帖 | `10 */4 * * *`（复盘后5分钟） | alpha_square_post_v2.py |

执行时间线（每天6次）：
```
00:00 scan → 00:05 review → 00:10 post
04:00 scan → 04:05 review → 04:10 post
08:00 scan → 08:05 review → 08:10 post
12:00 scan → 12:05 review → 12:10 post
16:00 scan → 16:05 review → 16:10 post
20:00 scan → 20:05 review → 20:10 post
```

## 复盘闭环流程

```
1. 获取涨跌幅榜  → surf market-ranking --sort-by change_24h
2. 获取Alpha列表 → Binance Alpha API (bapi/defi/v1/...)
3. 解析扫描信号 → 从 alpha_scan_output.md 提取
4. 漏扫分析     → 对比涨跌幅榜，分4类原因
5. 信号复盘     → 扫到的币是否暴涨>20%
6. 自动调参     → 根据漏扫原因修改阈值
7. 生成推文     → 写入 alpha_surge_tweets.json
```

## 漏扫原因分类

| 原因 | 含义 | 自动调参动作 |
|------|------|------------|
| `NOT_IN_ALPHA_LIST` | 非Alpha代币（MEME/合约币/新上线） | EXTEND_TO_MEME=True |
| `IN_ALPHA_LOW_SCORE` | 在Alpha但Score低 | MIN_SCORE 100→80 |
| `IN_ALPHA_POOR_LIQ` | Alpha但流动性差 | MIN_LIQUIDITY 50000→25000 |
| `FILTERED_BY_CURRENT_SCAN` | Alpha+流动性够但被过滤 | MIN_SCORE 100→70 |

## 自动调参逻辑

```python
# 触发条件
if IN_ALPHA_LOW_SCORE.count >= 3:
    MIN_SCORE = max(80, MIN_SCORE - 10)

if IN_ALPHA_POOR_LIQ.count >= 3:
    MIN_LIQUIDITY = max(20000, MIN_LIQUIDITY // 2)

if NOT_IN_ALPHA_LIST.count >= 5:
    EXTEND_TO_MEME = True

# 参数文件
PARAM_FILE = "/home/ubuntu/.hermes/scripts/alpha_params.json"
IMPROVE_LOG = "/home/ubuntu/.hermes/scripts/alpha_improve_log.json"
```

## 信号暴涨复盘

触发条件：`current_change > 20%`

推文格式：
```
🚀 $XXX 暴涨 +XX%！
⏰ XX/XX HH:MM Hermes监控到此信号
⏱ 距信号 X小时 🚀ATH
#BinanceAlpha #AlphaSignal #HermesRadar
```

## 输出文件

| 文件 | 内容 |
|------|------|
| `alpha_review_loop_report.json` | 完整复盘报告（含命中率/漏扫/暴涨/调参） |
| `alpha_surge_tweets.json` | 暴涨代币推文内容数组 |
| `alpha_params.json` | 当前阈值参数 |
| `alpha_improve_log.json` | 调参历史 |

## Binance Alpha API

**当前可用端点**：
```
https://www.binance.com/bapi/defi/v1/public/alpha-trade/aggTicker24?dataType=aggregate
```

响应字段：
```python
{
  "symbol": "NEX",
  "contractAddress": "0x...",
  "score": 85,
  "percentChange24h": 48.5,   # 不是 priceChangePercent！
  "marketCap": 23000000,
  "liquidity": 500000,
  "holders": ...,
}
```

## 已知问题

- `surf market-ranking` 参数必须用**空格**分隔（不是`=`）
- `alpha_square_post_v2.py` 的 Square API Key 可能失效（需更新）
