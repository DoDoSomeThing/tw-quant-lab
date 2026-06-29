#!/usr/bin/env python3
"""
營收 surprise 離線回測。訊號:月營收 YoY(或加速度)→ avail_date 後首個交易日進場 → 持有 N 日。
驗:YoY 分層 / regime 濾網 / 分年 / IS-OOS / 成本敏感;基準先用 0050 再用全市場等權。
用法: python studies/revenue_offline_bt.py
"""
import os
import sys
import bisect
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from framework import config
from framework.data import Data, load_revenue
from framework import gates

COST = config.COST
BENCH = config.BENCH
HOLD = 20

D = Data()
cal, C, regime = D.cal, D.C, D.regime
rev = load_revenue()


def yr(x):
    return x[:4]


# 建事件:每檔每月算 YoY + 加速度
events = []  # (sid, entry_date, yoy, accel)
for sid, rows in rev.items():
    if sid == BENCH or sid not in C:
        continue
    by_ym = {r[1]: r[2] for r in rows if r[2]}
    rows_s = sorted(rows, key=lambda r: r[1])
    yoy_hist = {}
    for avail, ym, r in rows_s:
        if not r:
            continue
        prev = by_ym.get(ym - 100)
        if not prev or prev <= 0:
            continue
        yoy = r / prev - 1
        yoy_hist[ym] = yoy
        prev3 = [yoy_hist[ym - 100 * k] for k in (1, 2, 3) if (ym - 100 * k) in yoy_hist]
        accel = (yoy - statistics.mean(prev3)) if len(prev3) == 3 else None
        ed = D.next_td(avail)
        if ed is None or ed < cal[0] or ed > cal[-1]:
            continue
        events.append((sid, ed, yoy, accel))

print("=" * 68)
print(f"營收事件 {len(events)} 筆  資料股數 {len(rev)}  基準=0050  持有{HOLD}日 成本{COST*100:.1f}%")
print("=" * 68)


def hold_excess(sid, ed, cost=COST):
    i = bisect.bisect_left(cal, ed)
    if i + HOLD >= len(cal):
        return None
    e, x = cal[i], cal[i + HOLD]
    if e not in C[sid] or x not in C[sid] or C[sid][e] <= 0:
        return None
    bret = D.bench_fwd(e, x)
    if bret is None:
        return None
    return (C[sid][x] / C[sid][e] - 1 - cost) - bret


def summ(name, sub):
    pairs = [(ed, hold_excess(sid, ed)) for sid, ed, *_ in sub]
    gates.report(name, pairs)


summ("全部事件", events)

print("\n— YoY 分層 —")
for lo, hi, lab in [(-9, 0, "YoY<0"), (0, 0.2, "0~20%"), (0.2, 0.5, "20~50%"), (0.5, 9, ">50%")]:
    summ(lab, [(s, e, y, a) for s, e, y, a in events if y is not None and lo <= y < hi])

print("\n— 高YoY(>20%) × regime 濾網 —")
hi = [(s, e, y, a) for s, e, y, a in events if y is not None and y >= 0.2]
summ("高YoY 全市況", hi)
summ("高YoY 只非空頭", [(s, e, y, a) for s, e, y, a in hi if regime.get(e) is True])
summ("高YoY 只空頭", [(s, e, y, a) for s, e, y, a in hi if regime.get(e) is False])

print("\n— 加速度(YoY增速)>0 × regime —")
acc = [(s, e, y, a) for s, e, y, a in events if a is not None and a > 0]
summ("加速>0 全市況", acc)
summ("加速>0 只非空頭", [(s, e, y, a) for s, e, y, a in acc if regime.get(e)])

print("\n— 成本敏感(高YoY非空頭)—")
hn = [(s, e, y, a) for s, e, y, a in hi if regime.get(e)]
for c in (config.COST_LO, config.COST_HI):
    pairs = [(ed, hold_excess(s, ed, c)) for s, ed, *_ in hn]
    pairs = [(d2, v) for d2, v in pairs if v is not None]
    if pairs:
        v = [x for _, x in pairs]
        print(f"  成本{c*100:.1f}%: 超額均={statistics.mean(v)*100:+.2f}% "
              f"勝率={sum(1 for x in v if x > 0)/len(v)*100:.0f}%")

# ===== 改用「全市場等權」當基準(剔除台積電權值,測選股本身技術)=====
print("\n" + "=" * 68)
print("【改用 全市場等權 當基準】(0050 太強,改問:有沒有贏台股平均)")
print("=" * 68)
EW = D.build_ew(HOLD)


def hold_excess_ew(sid, ed, cost=COST):
    i = bisect.bisect_left(cal, ed)
    if i + HOLD >= len(cal):
        return None
    e, x = cal[i], cal[i + HOLD]
    if e not in EW or e not in C[sid] or x not in C[sid] or C[sid][e] <= 0:
        return None
    return (C[sid][x] / C[sid][e] - 1 - cost) - EW[e]


def summ_ew(name, sub):
    pairs = [(ed, hold_excess_ew(sid, ed)) for sid, ed, *_ in sub]
    gates.report(name, pairs)


summ_ew("全部事件", events)
summ_ew("YoY 20~50%", [(s, e, y, a) for s, e, y, a in events if y is not None and 0.2 <= y < 0.5])
summ_ew("高YoY>20% 全市況", hi)
summ_ew("高YoY>20% 非空頭", [(s, e, y, a) for s, e, y, a in hi if regime.get(e) is True])
print("\n看:vs等權 若高YoY 穩定正(含OOS)→ 選股本身有技術(只是贏不過台積電權值的0050)。")

# ===== 中小型股 subset(edge 最可能藏的地方,vs 等權)=====
print("\n" + "=" * 68)
print("【高YoY×非空頭:大型 vs 中小型】(edge 最可能在中小型,vs 全市場等權)")
print("=" * 68)
med = D.size_pct(0.5)
hi_nonbear = [(s, e, y, a) for s, e, y, a in hi if regime.get(e) is True]
big = [ev for ev in hi_nonbear if D.avgvol.get(ev[0], 0) >= med]
small = [ev for ev in hi_nonbear if D.avgvol.get(ev[0], 0) < med]
summ_ew("大型(量≥中位)", big)
summ_ew("中小型(量<中位)", small)
q1 = D.size_pct(0.25)
tiny = [ev for ev in hi_nonbear if D.avgvol.get(ev[0], 1e18) < q1]
summ_ew("最小型(量後25%)", tiny)
print("\n看:若中小型/最小型 vs等權 明顯正且OOS不崩 → edge 在小型股(留意流動性/滑點)。")
