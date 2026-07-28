#!/usr/bin/env python3
"""
Wash Trading Detection — 地址对互惠流分析
用于检测 Alpha 代币的做市商刷量行为

用法:
    from hermes_tools import terminal
    import json
    CONTRACT = "0x4d6394bc3031f751edce368c189b0e060b527107"
    r = terminal(f'surf token-transfers --address "{CONTRACT}" --chain bsc --limit 50 2>/dev/null', timeout=60)
    transfers = json.loads(r['output']).get('data', [])
    pairs, signals = detect_wash_trading(transfers, holder_map={})
    print_signals(pairs, signals)
"""

from collections import defaultdict
from datetime import datetime

def detect_wash_trading(transfers, holder_map=None):
    """
    输入: transfers = surf token-transfers 返回的 .data[] 列表
          holder_map = {address: {"name": str, "type": str}} 可选
    返回: (pairs_dict, signals_list)
    """
    pairs = defaultdict(lambda: {'fwd': 0.0, 'rev': 0.0, 'total': 0.0, 'count': 0})

    for t in transfers:
        frm = t.get('from_address', '')
        to = t.get('to_address', '')
        amt = float(t.get('amount', 0))
        key = tuple(sorted([frm, to]))
        if frm < to:
            pairs[key]['fwd'] += amt
        else:
            pairs[key]['rev'] += amt
        pairs[key]['total'] += amt
        pairs[key]['count'] += 1

    signals = []
    for (a, b), data in sorted(pairs.items(), key=lambda x: -x[1]['total']):
        if data['fwd'] > 0 and data['rev'] > 0:
            ratio = data['fwd'] / data['rev'] if data['rev'] > 0 else float('inf')
            a_name = holder_map.get(a, {}).get('name', a[:16]) if holder_map else a[:16]
            b_name = holder_map.get(b, {}).get('name', b[:16]) if holder_map else b[:16]
            signals.append({
                'addr_a': a,
                'addr_b': b,
                'a_name': a_name,
                'b_name': b_name,
                'fwd': data['fwd'],
                'rev': data['rev'],
                'ratio': ratio,
                'total': data['total'],
                'count': data['count'],
                'wash_score': 1.0 - abs(1.0 - ratio) if 0.5 < ratio < 2.0 else 0.0
            })

    return pairs, signals

def print_signals(pairs, signals, min_total=500):
    """打印 Wash Trading 检测结果"""
    high_conf = [s for s in signals if s['wash_score'] > 0.7 and s['total'] > min_total]
    print(f"\n=== Wash Trading 检测 ===")
    print(f"总地址对数: {len(signals)}")
    print(f"高可信 Wash 对 (ratio 0.7-1.3, total>{min_total}): {len(high_conf)}")

    for s in sorted(high_conf, key=lambda x: -x['total']):
        ratio = s['ratio']
        print(f"  {s['a_name'][:20]} ↔ {s['b_name'][:20]}")
        print(f"    fwd={s['fwd']:.1f}, rev={s['rev']:.1f}, ratio={ratio:.2f}x, total={s['total']:.1f}, count={s['count']}")

    return high_conf

def detect_time_cluster(transfers, time_window_sec=120):
    """检测时间集中度 — 大量转账集中在短时间窗口内是 Wash Trading 特征"""
    if not transfers:
        return []
    timestamps = sorted([int(t.get('timestamp', 0)) for t in transfers if t.get('timestamp')])
    if len(timestamps) < 2:
        return []

    gaps = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
    max_gap = max(gaps) if gaps else 0
    total_span = timestamps[-1] - timestamps[0]
    density = len(transfers) / (total_span / 3600) if total_span > 0 else 0  # tx/hr

    print(f"\n=== 时间集中度 ===")
    print(f"  总 span: {total_span}s ({total_span/60:.1f}min)")
    print(f"  最大间隔: {max_gap}s")
    print(f"  密度: {density:.1f} tx/hr")

    if total_span < time_window_sec:
        print(f"  🔴 所有转账集中在 {total_span}s 内 — 高可信 Wash Trading")
    return []

if __name__ == '__main__':
    # 测试用示例
    import json, sys
    from hermes_tools import terminal

    if len(sys.argv) < 3:
        print("Usage: wash-trading-detection.py <contract> <chain>")
        sys.exit(1)

    contract, chain = sys.argv[1], sys.argv[2]
    r = terminal(f'surf token-transfers --address "{contract}" --chain {chain} --limit 50 2>/dev/null', timeout=60)
    data = json.loads(r['output'])
    transfers = data.get('data', [])

    # 同时拉 holders 做地址映射
    rh = terminal(f'surf token-holders --address "{contract}" --chain {chain} --limit 50 2>/dev/null', timeout=20)
    hd = json.loads(rh['output']).get('data', [])
    holder_map = {h['address']: {'name': h.get('entity_name', ''), 'type': h.get('entity_type', '')} for h in hd}

    pairs, signals = detect_wash_trading(transfers, holder_map)
    high_conf = print_signals(pairs, signals)
    detect_time_cluster(transfers)
