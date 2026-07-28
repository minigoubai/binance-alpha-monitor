# O Token 预上市陷阱案例（2026-07-29）

## 背景

用户查询 O Token（o1.exchange）筹码流向。

## Alpha API 数据（查询时实时获取）

| 字段 | Alpha API 值 |
|------|-------------|
| symbol | O |
| contractAddress | `0x500a02a20b0b0a3f3efccfc0559543f5743bd1c4` |
| score | 111（满分） |
| price | $0.4844 |
| percentChange24h | +4.98% |
| volume24h | $5,781,056 |
| liquidity | $2,480,350 |
| marketCap | $77,510,043 |
| fdv | $484,437,767 |
| totalSupply | 1,000,000,000 O |
| circulatingSupply | 160,000,000 O |
| holders | 34,664 |
| listingTime | 1781704800000（2026-06-17 22:00） |
| canTransfer | false |
| listingCex | false |
| onlineAirdrop | true |

## 链上实际状态（同一时刻 BSC RPC 查询）

### 合约存在性
```
eth_getCode(0x500a02a2...) at block 112,665,464 → len=25034（✅ 合约存在）
```

### 部署时间
```
部署区块: 112,665,182
部署时间: 2026-07-29 01:21:56（距查询仅 127 秒）
部署者: 0x13d5848fe005b41614d65fd14f691134f37e9c06
部署TX: 0x60aeb8a71771a9c4129836e1b5e190118405b678e653a8e4c23b9d1597d141fb
```

### 链上 totalSupply
```
totalSupply(): 20,758,605,360,661,000,000,000,000 = 20,758,605.36 O
（Alpha 报告 1,000,000,000，差异 48 倍）
```

### 链上事件
```
eth_getLogs(Transfer events, fromBlock=deployment, toBlock=latest) → 0 笔
```

### DEX 交易对
```
PCS Factory.getPair(O, WBNB) → 0x0（全零地址，无交易对）
PCS Factory.getPair(O, BUSD) → 无
PCS Factory.getPair(O, USDT) → 无
ApeSwap/BabySwap 同样无对
```

### view function 响应（刚部署正常现象）
```
decimals() → 0x...12 ✅ 正常
totalSupply() → ✅ 正常
name() → 全0（constructor 未显式设置）
symbol() → 全0（constructor 未显式设置）
balanceOf(any) → 0（无人转账）
transfer() → 无响应（canTransfer=false 状态）
```

## 关键教训

1. **Alpha listingTime ≠ 链上部署时间**：Alpha 系统提前 42 天预标记，真正的链上合约今天才部署
2. **Alpha holders ≠ 链上真实持仓**：34,664 地址是 Alpha 内部预撮合记录，链上 0 笔 Transfer 证明无人真实持仓
3. **Alpha liquidity ≠ DEX 真实流动性**：无 PCS 对，$2.48M 流动性来源不明（Alpha 内部系统撮合）
4. **FDV 严重失真**：链上只有 20.76M O 在流通，Alpha 用 10 亿 FDV 计算当前价，溢价 48 倍

## 命令记录

```python
# 查合约代码
rpc_call("eth_getCode", [ADDR, "latest"])

# 查部署区块（二分查找）
low, high = 111500000, current_block
while high - low > 1000:
    mid = (low + high) // 2
    if len(rpc_call("eth_getCode", [ADDR, hex(mid)]) > 10:
        high = mid
    else:
        low = mid

# 查链上 totalSupply
rpc_call("eth_call", [{"to": ADDR, "data": "0x18160ddd"}, "latest"])

# 查 PCS pair
FACTORY = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
rpc_call("eth_call", [{"to": FACTORY, "data": getPair_data}, "latest"])

# 查 Transfer events（BSC RPC limit=500）
rpc_call("eth_getLogs", [{
    "fromBlock": hex(deploy_block),
    "toBlock": "latest",
    "address": ADDR,
    "topics": ["0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"],
    "limit": 500
}])
```

## 结论

**预上市代币（Alpha 标记 canTransfer=false）无法做链上筹码分析**，必须等合约开放转账后再追踪。分析此类代币的正确做法：

1. 通过 `eth_getCode` 确认合约是否真实存在
2. 通过 deployment block 时间戳确认真实部署时间
3. 通过 `eth_getLogs` 确认是否有真实链上转账
4. 如果以上均为"刚部署/无转账"，结论：数据不足，暂无法分析
