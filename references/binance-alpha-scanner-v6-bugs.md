# binance_alpha_scanner_v6_arkham.py — 已知问题 & 修复记录

## 运行状态

✅ 脚本可正常执行（2026-06-21 验证）
- Python 输出缓冲问题：加 `PYTHONUNBUFFERED=1` 或 `python3 -u`
- surf install 报 "text file busy" 重试即可（`surf sync` 成功）

---

## Bug 1（已修复）：flow_symbols 阈值108 → B+候选跳过 tech/tokenomics

**症状**：`format_hermes_alert_v6` 里 `tech_indicators=None`，RSI/解锁数据全显示"待补"。B+级警报（MET/XPL/BANANAS31）都有此问题。

**根因**：`flow_symbols` 阈值 `Score≥108` 太高，B+候选（Score 50-65）被排除 → 这批代币从未进入 `do_flow=True` 分支 → `get_token_tech_indicators` 和 `get_token_tokenomics` 从未被调用。

**修复**（2026-06-21）：
```python
# 修复前
flow_tokens = [r for r in sorted_by_score if float(r["token"].get("score", 0)) >= 108 ...][:20]

# 修复后
flow_tokens = [r for r in sorted_by_score if float(r["token"].get("score", 0)) >= 50 ...][:30]
```

---

## Bug 2（已修复）：HFTZ数据存在但"筹码: 待补"

**症状**：HFTZ五分法数据已经抓取并传入 `format_hermes_alert_v6`，但报告中 `证据 → 筹码: 待补` 依然出现。

**根因**：`format_hermes_alert_v6` 里 `evidence["筹码动向"]` 读的是 Arkham `smart_money_flow` 槽（由 `get_token_transfer_flow` 填充）。当 Arkham 无 entity_label 数据时，这个列表一直是空的。HFTZ 数据传进来了但写入了 `evidence["HFTZ筹码"]` 槽，不是 `evidence["筹码动向"]` 槽——两槽在报告模板里是分开显示的。

**修复**（2026-06-21）：在 `format_hermes_alert_v6` 里，HFTZ数据存在时直接覆盖 `evidence["筹码动向"]`：
```python
if hftz_data:
    hf = hftz_data
    hf_signal = hf.get("combined_signal", "neutral")
    hf_verdict = hf.get("verdict", "WATCH")
    op_pct = hf.get("holders", {}).get("operator_pct", 0.0)
    cex_pct = hf.get("holders", {}).get("cex_pool_pct", 0.0)
    retail_pct = hf.get("holders", {}).get("verifiable_retail_pct", 0.0)
    sig_icon = "🟢" if hf_signal == "accumulating" else ("🔴" if hf_signal == "distributing" else "⚪")
    evidence["筹码动向"] = [
        f"{sig_icon} HFTZ信号: {hf_signal} | 判定: {hf_verdict}",
        f"三分法: Operator {op_pct:.1f}% | CEX Pool {cex_pct:.1f}% | 零售 {retail_pct:.1f}%",
    ]
    # ... proxy/dumper/flags ...
```

---

## 已知限制（非bug）

### Arkham entity_label 在 Surf free tier 不可用

`_classify_by_entity` 依赖 `from_label.entity_type` 字段做交易所/VC/鲸鱼分类。但 Surf 的 `token-transfers` free tier 返回的每条记录里没有 `entity_label` → 几乎所有转账都被归为 `"unknown"` → `evidence["筹码动向"]` 全空。

**当前 workaround**：依赖 HFTZ 五分法数据覆盖此槽。

### RSI/解锁数据覆盖不完整

`get_token_tech_indicators`（RSI）只对 Binance 主交易对有效，大多数 Alpha 小币种无数据。`get_token_tokenomics` 需要项目方配置解锁日程，部分代币也返回空。

---

## 脚本文件路径

```
/home/ubuntu/.hermes/scripts/binance_alpha_scanner_v6_arkham.py
```

## 快速验证命令

```bash
# 验证脚本能跑（不加 PYTHONUNBUFFERED 可能无输出）
PYTHONUNBUFFERED=1 python3 /home/ubuntu/.hermes/scripts/binance_alpha_scanner_v6_arkham.py

# 检查报告
cat /home/ubuntu/.hermes/scripts/alpha_scan_output.md
```
