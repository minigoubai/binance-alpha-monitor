# Binance Alpha — Scan Review Publish Pipeline

> Absorbed from `binance-alpha` skill (2026-07-08). The core forensic framework lives in the parent `SKILL.md`; this reference covers the operational cron pipeline.

## Architecture — Three-Script 闭环

```
binance_alpha_scanner_v6_arkham.py  →  alpha_review_loop.py  →  alpha_square_post_v2.py
     (扫描)                              (复盘闭环)                  (广场发帖)
```

| Script | Cron | Key Responsibility |
|--------|------|---------------------|
| `binance_alpha_scanner_v6_arkham.py` | `0 */4 * * *` | Alpha API scan → top signals → DM report |
| `alpha_review_loop.py` | `5 */4 * * *` | Top movers by 24h change → deep chip analysis → stage judgment |
| `alpha_square_post_v2.py` | `10 */4 * * *` | Generate + publish Square post (≤3涨+3跌 tokens) |

## Scanning Frequency

每天6次（0/4/8/12/16/20点 UTC）。

## User Preferences (Confirmed 2026-07-05)

- 用户说中文，动手型（不需要每步确认）
- 只发 DM，不发群组消息
- 链上车头监控单独推送，不合并到 Alpha 复盘
- **Square 发帖核心要求**：
  - 分析对象：24h暴涨暴跌的代币（不问来源），分析筹码分布 + 当前阶段（收集/拉升/出货/砸盘/整理）
  - 禁止内容：合约地址、漏扫原因分析
  - 格式：纯文本紧凑，每代币3行，无emoji/无markdown符号
  - 数据来源：DexScreener（市场数据）+ surf token-holders（筹码，额度耗尽时标注"不可用"）
  - surf 额度耗尽时，用市场数据（买卖比/涨跌幅度）估算阶段，不因此放弃分析

## Key Files

- `/home/ubuntu/.hermes/scripts/alpha_review_loop.py` — 复盘闭环。选Top3涨+Top3跌 → DexScreener + surf holders → 阶段判断（拉升/收集/出货/砸盘/整理）→ 保存快照。surf额度耗尽时用市场数据估算阶段。
- `/home/ubuntu/.hermes/scripts/alpha_square_post_v2.py` — 广场发帖。读取 alpha_review_loop_report.json → 生成紧凑内容（≤550 chars，≤6个代币，每代币3行）→ 发帖到Binance Square。彻底移除漏扫分析和合约地址。
- `/home/ubuntu/.hermes/scripts/alpha_chip_snapshots.json` — 筹码快照历史（供对比用）
- `/home/ubuntu/.hermes/scripts/alpha_review_loop_report.json` — 复盘结果（含阶段判断+市场数据+筹码数据）

## Square Posting — Content Format

**Post type: Alpha 暴涨暴跌筹码分析**

### What to Include
- 各取24h涨幅最大3个 + 跌幅最大3个代币
- 每行代币：代币名(链) | 24h涨跌幅 | 当前阶段（拉升/收集/出货/砸盘/整理） | 风险标记
- 筹码一行：Top5集中度 / CEX占比 / DEX占比 / Top1单地址
- 市场数据一行：市值 / 流动性 / 市值流动性倍数 / 24h买卖笔数

### What NOT to Include
- ❌ 合约地址（任何时候都不出现在 Square post 里）
- ❌ 漏扫原因分析（用户明确禁止）
- ❌ 冗余空行、emoji、markdown符号

### Posting Priority Cascade
```
IF top_movers 存在 → 发 "暴涨暴跌筹码分析" 内容（3涨+3跌，最多6个）
ELSE → 发简洁兜底帖
```

### Content Length Cap

Binance Square enforces a **~2000 character limit** on `bodyTextOnly` content.
Strategy: 最多6个代币（3涨+3跌），每代币3行，无多余空行、无emoji、无markdown符号。
实测：3涨+2跌 ≈ 550 chars — safe.

### Binance Square API

```
POST https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add
Headers: X-Square-OpenAPI-Key: <key>, Content-Type: application/json, clienttype: binanceSkill
Body: {"bodyTextOnly": "<full article with # title>"}
```
- Article format: title IS the first line (already has `#`), content generators should NOT add `#` prefix
- Response: `{"code": "000000", "data": {"id": <post_id>}}`

## Bug History (Do Not Reintroduce)

1. **乱码问题**：`bodyTextOnly` 模式下 emoji + `*` markdown 符号渲染成一团乱码。修复：全部用纯中文文字，无任何 markdown 符号。
2. **涨幅数据错误**：Alpha API 的 `percentChange24h` 经常是 0%。修复：改用 DexScreener 实时数据获取真实 24h 涨幅。
3. **无效符号**：原发帖内容含大量 `|` `***` `->>` 等乱码符号。修复：内容生成器全部重写为纯文本。
4. **复盘内容空洞**：原复盘只对比价格，没有链上筹码分析。修复：`alpha_review_loop.py` 重写为深度筹码分析流程。
5. **内容超限（20013错误）**：2026-07-05 实测 12个代币+冗余格式 = 2800+ chars 超 Binance Square 限制。修复：精简为3涨+3跌，每代币3行紧凑格式，总长控制在600 chars以内。
6. **漏扫原因分析被用户禁止**：用户明确要求不发"漏扫原因"，不发合约地址，要分析暴涨暴跌代币的筹码和阶段。

## Telegram 推送模式

> 详见 `references/telegram-push-pattern.md`（DM 发送方式 + Bot Token 来源）

Cron 任务的 `alpha_combined_report.md` 完成后，直接将最终响应作为报告内容即可。**不需要**再用 `hermes send` 推送到同一个 channel — cron 会自动把最终响应推送到配置的 `deliver` 目标。
