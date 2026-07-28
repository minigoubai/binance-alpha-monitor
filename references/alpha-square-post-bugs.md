# Alpha Square 发帖脚本 Bug 记录

> 位于 `alpha_square_post.py`（`/home/ubuntu/.hermes/scripts/alpha_square_post.py`）

---

## Bug 1（已修复）：信号解析正则灾难性回溯 → ZBT/HEMI 的 signal 返回 None

**症状**：BREV 信号正常（🟢 建仓偏多），但 ZBT/HEMI 的 `ic["signal"]` 全为 `None`，导致证据摘要走 `chip_text` fallback（截断且不完整）。

**根因**：正则使用了宽泛的 `(?=\n\*\*|\n---|\n###)` 作为终止断言，但 `\n**` 过于模糊——报告里每个加粗标题前都有 `\n\n**`，导致回溯路径爆炸。

**错误正则**：
```python
signal_match = re.search(r'\*\*信号\*\*:\s*([🟢🔴⚪])\s*(\S+)(.*?)(?=\n\*\*|\n---|\n###)', chunk)
signal_full = (signal_match.group(1) + " " + signal_match.group(2) + signal_match.group(3)).strip().rstrip("|").strip() if signal_match else None
```

**修复后正则**（精确截止到 `**理由**:` 标题行）：
```python
signal_match = re.search(r'\*\*信号\*\*:\s*([🟢🔴⚪])\s*(.*?)\n\n\*\*理由\*\*:', chunk, re.DOTALL)
signal_full = (signal_match.group(1) + " " + signal_match.group(2).rstrip()).strip() if signal_match else None
```

**验证**：修复后 ZBT signal=`"🔴 出货 | 生命周期: 早期分配阶段 | 信心: 中"`（完整），HEMI signal=`"⚪ 中性 | 生命周期: 筹码积累期 | 信心: 低"`（完整）。

---

## Bug 2（已修复）：证据摘要词中截断 → 阅读不畅

**症状**：证据文本在120字符处硬截断，把单词拦腰截断——`CEX净流出仅 $858...`、`买盘/卖盘比仅 0.0...`、`DEX买卖比 = 1.01x（完全均...`。

**错误逻辑**：
```python
if len(reason) > 120:
    reason = reason[:120] + "..."
```

**修复后逻辑**（按标点断句）：
```python
if len(reason) > 180:
    cutoff = 180
    for punct in ('。', '，', '. ', ', '):
        last_punct = reason.rfind(punct, 0, 180)
        if last_punct > 120:
            cutoff = last_punct + 1
            break
    reason = reason[:cutoff] + "..."
```

**效果**：ZBT 证据从"CEX净流出 $186,930..."（词中截断）变为"DEX买盘/卖盘比仅 **0.04x**（严重卖盘主导）"（完整句子）。

---

## 相关陷阱（未修复，但已知）

### reason_match 正则终止条件同样存在回溯风险

`reason_match` 使用的终止断言 `(?=\n\*\*|\n---|\n###)` 与 signal_match 相同，但 reason 块通常内容较短（5-10行），实际未触发超时。已知风险：若代币理由文本很长，可能同样回溯爆炸。

**已知安全边界**：reason 通常5行，每行~50字，总长~250字以内，`re.DOTALL` + 非贪婪 `.*?` + `\n---` 终止在此长度下可正常工作。

---

## 验证方法

```python
python3 -c "
import sys; sys.path.insert(0, '/home/ubuntu/.hermes/scripts')
from alpha_square_post import read_report, parse_report, generate_article
content = read_report('/home/ubuntu/.hermes/scripts/alpha_combined_report.md')
data = parse_report(content)
for sym in ['BREV', 'ZBT', 'HEMI']:
    ic = data['inchain'].get(sym, {})
    assert ic.get('signal') is not None, f'{sym} signal is None!'
    assert len(ic.get('reason', '')) > 0, f'{sym} reason is empty!'
print('✅ All signals parsed correctly')
"
```
