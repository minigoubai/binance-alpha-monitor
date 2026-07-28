# surf market-ranking — 正确语法

## ⚠️ 致命陷阱：参数用 `=` 还是空格

Surf CLI 的 sub-command 参数 **必须用空格分隔**，不能用 `=`。

```bash
# ❌ 错误：用等号连接 → "unknown command"
surf market-ranking --sort-by=change_24h --order=desc --limit=20

# ✅ 正确：空格分隔
surf market-ranking --sort-by change_24h --order desc --limit 20
```

症状：`ERROR: Error: unknown command "market-ranking ..."`

## 验证有效的命令格式

```bash
# 24h涨幅榜
surf market-ranking --sort-by change_24h --order desc --limit 20 --json

# 24h跌幅榜
surf market-ranking --sort-by change_24h --order asc --limit 20 --json

# 按市值排序（默认）
surf market-ranking --order desc --limit 20
```

## 响应结构

返回格式：
```json
{
  "data": [  // ← 数据在这里，不是 .items[]
    {
      "symbol": "NEX",
      "name": "NEX",
      "price_usd": 0.023,
      "change_24h_pct": 48.48,
      "market_cap_usd": 23000000,
      "volume_24h_usd": 1500000,
      "rank": 1,
      ...
    }
  ],
  "meta": {
    "cached": false,
    "credits_used": 1,
    "limit": 20,
    "offset": 0
  }
}
```

## 关键字段映射

| surf 字段 | 含义 |
|-----------|------|
| `change_24h_pct` | 24h价格变化百分比 |
| `market_cap_usd` | 市值（USD） |
| `volume_24h_usd` | 24h成交量 |
| `price_usd` | 当前价格（USD） |

## category filter

可选，按板块过滤：
```bash
surf market-ranking --sort-by change_24h --order desc --category MEME --limit 20
```
可用类别：`MEME`, `AI`, `AI_AGENTS`, `L1`, `L2`, `DEFI`, `GAMING`, `STABLECOIN`, `RWA`, `DEPIN`, `SOL_ECO`, `BASE_ECO`, `LST`

## 在 Python 里调用

```python
def surf_market_ranking(order="desc", limit=20, sort_by="change_24h"):
    r = subprocess.run(
        f"surf market-ranking --sort-by {sort_by} --order {order} --limit {limit}",
        shell=True, capture_output=True, text=True, timeout=30
    )
    data = json.loads(r.stdout)
    return data.get("data", [])  # 不是 .items[]

gainers = surf_market_ranking("desc")
```

## 常见错误排查

| 错误 | 原因 | 修复 |
|------|------|------|
| `unknown command` | 参数用了 `=` | 改用空格 |
| `Cannot iterate over null` | jq 用 `.items[]` | 用 `.data[]` |
| `{'data': []}` 全空 | --sort-by 值错误 | 用 `change_24h`（不是 `change_24h_pct`） |
