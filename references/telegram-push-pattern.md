# Telegram 推送模式

## Cron 自动投递（推荐）

Cron 任务的最终响应即为推送内容。**不要**在 cron 里用 `hermes send` 推送到同一目标 — 会触发 "already auto-deliver" 警告。

```
最终输出 = cron 响应 → 自动推送到 deliver 目标（telegram:chat_id:thread）
```

## 手动 Bot API 推送（直接发 Telegram）

当需要直接调用 Telegram Bot API 时，用 Python + curl 脚本：

### Bot Token 来源

从 `wallet_tracker.py` 或 `zhuangjia_tracker.py` 等脚本中提取：

```
BOT_TOKEN = "8772201782:AAEHNcVeAy1KTtE9GXD9Vfm_Pot39_UDaf0"
CHAT_ID = "-1003779518465"       # 频道 ID（负数）
CHANNEL_THREAD = "4294968302"    # 话题 ID（threaded chat）
```

### Python curl 脚本模板

```python
#!/usr/bin/env python3
import json, subprocess

BOT_TOKEN = "8772201782:AAEHNcVeAy1KTtE9GXD9Vfm_Pot39_UDaf0"
CHAT_ID = "-1003779518465"
CHANNEL_THREAD = "4294968302"
url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

with open('/path/to/report.md') as f:
    content = f.read()

MAX_MSG = 4000
msgs = []
while len(content) > MAX_MSG:
    idx = content[:MAX_MSG].rfind('\n')
    if idx < 100:
        idx = MAX_MSG
    msgs.append(content[:idx])
    content = content[idx:].lstrip('\n')
msgs.append(content)

for i, part in enumerate(msgs):
    parse_mode = 'Markdown' if i == 0 and len(msgs) > 1 else None
    data = {
        'chat_id': CHAT_ID,
        'message_thread_id': CHANNEL_THREAD,
        'text': (f"[{i+1}/{len(msgs)}]\n\n" if len(msgs) > 1 else '') + part,
    }
    if parse_mode:
        data['parse_mode'] = parse_mode
    r = subprocess.run(
        ['curl', '-s', '-X', 'POST', url,
         '-d', json.dumps(data),
         '-H', 'Content-Type: application/json'],
        capture_output=True, text=True
    )
    resp = json.loads(r.stdout)
    print(f"Part {i+1}: {'OK' if resp.get('ok') else resp}")
```

### ⚠️ Markdown entity split Bug（4000字符截断 bisect `**` bold）

当分块策略按 `\n` 找截断点时，若恰好在 `**bold**` 序列中间切开：

```
Part 1 末尾: ...**信号**: ⚪ 中性 | 生
Part 2 开头: 命周期: 盘整期 | 信心: 中** ← Telegram 找不到开头**
```

报错：
```
Bad Request: can't parse entities: Can't find end of the entity starting at byte offset 1884
```

**正确做法：Part 1 保持 Markdown，Part 2 开始全部去掉 `parse_mode` 发纯文本。**

```python
for i, part in enumerate(msgs):
    # ❌ 错误：Part 2 也发 Markdown
    data['parse_mode'] = 'Markdown'   # Part 2 有 bisected ** 必崩

    # ✅ 正确：Part 1 有完整 Markdown，Part 2+ 全纯文本
    if i == 0:
        data['parse_mode'] = 'Markdown'
    # else: 不加 parse_mode 字段 → Telegram 当纯文本处理
```

**根本原因**：Telegram Markdown 解析器遇到截断的 `**` 就报 offset 错误，不会跳过。宁可全段都不用 Markdown，也不要让后半截带着破损的 `**` 发送。

### 调试技巧

```bash
# 手动测试 Token 有效性
curl -s "https://api.telegram.org/bot{BOT_TOKEN}/getMe"

# 查看频道消息
curl -s "https://api.telegram.org/bot{BOT_TOKEN}/getChat?chat_id=${CHAT_ID}"
```
