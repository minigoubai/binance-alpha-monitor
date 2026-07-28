# Alpha 复盘闭环已知问题

> 与 `references/alpha-review-pipeline.md` 配套，记录已知 bug 和修复方案。

---

## Bug 1（已识别）：SURGE_REPLAY 模式误将新信号判为复盘

**文件**：`alpha_review_loop.py`

**症状**：新扫到的代币（ZKP，hours_since_signal=0.0）被放入 `surge_replays` 数组，模式显示为 `SURGE_REPLAY`，发帖内容变成"距信号 0.0 小时"。

**根因**：对比扫描信号时，新信号的时间戳等于复盘时间，导致 `hours_since_signal=0.0`。逻辑没有区分"历史信号本轮暴涨"和"本轮新信号"。

**修复方案**：
```python
# 在 compute_hours_since_signal 后加判断
if hours_since_signal <= 0.1:  # 几乎同时（<6分钟）
    surge_mode = "NEW_SIGNAL_SURGE"  # 本轮新信号首次被发现
    time_desc = f"本轮扫描首次发现（{scan_time}）"
else:
    surge_mode = "SURGE_REPLAY"       # 历史信号本轮暴涨
    time_desc = f"距信号 {hours_since_signal:.1f} 小时"
```

**推文格式（两种模式）**：
- SURGE_REPLAY：`🚀 $XXX 暴涨 +XX%！⏰ XX/XX HH:MM Hermes监控到此信号⏱ 距信号 X小时🚀ATH`
- NEW_SIGNAL_SURGE：`🆕 $XXX 初现信号 +XX%！⏰ XX/XX HH:MM 首次在Alpha扫描中发现`

---

## Bug 2（已修复）：Square API header 格式错误

**文件**：`alpha_square_post_v2.py`

**症状**：发帖返回 `code: 220003 "API key not found"`，但 `alpha_square_post.py`（旧版）同一时间成功。

**根因**：v2 用了 `X-OpenAPI-Key` 或错误的 payload 字段，`bodyTextOnly` 字段缺失。

**修复**：参考旧版格式：
```python
# ✅ 正确 header
headers = {
    "X-Square-OpenAPI-Key": SQUARE_API_KEY,
    "Content-Type": "application/json"
}

# ✅ 正确 payload（不是 title/content/tags）
payload = {
    "bodyTextOnly": True,
    "text": article_content  # 直接放文本，不是 body 字段
}
```

**验证**：`code: 000000` + `post_id` 返回 = 成功。
