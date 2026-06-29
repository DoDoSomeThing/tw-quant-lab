#!/usr/bin/env python3
"""
深榨唯一活的線:大型 × 高YoY(>20%) × 非空頭(0050>MA120)。
疊價格確認濾網(MA60 / 動能 / 新高 / 法人進場前淨買),看超額能否放大、勝率過50%。基準=等權。
用法: python studies/revenue_refine.py
"""
import os
import sys
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from framework import config
from framework.data import Data, load_revenue, load_t86
from framework import gates

COST = config.COST
BENCH = config.BENCH
HOLD = 20

D = Data()
cal, C, cidx, regime = D.cal, D.C, D.cidx, D.regime
rev = load_revenue()
EW = D.build_ew(HOLD)
med = D.size_pct(0.5)
INST = load_t86()
import glob
N_T86 = len(glob.glob(os.path.join(config.T86_DIR, "*.json")))


def yr(x):
    return x[:4]


def inst_buy(sid, ed):
    i = cidx.get(ed)
    if i is None or i == 0:
        return False
    v = INST.get(sid, {}).get(cal[i - 1])
    return bool(v and (v[0] > 0 or v[1] > 0))


# 建基礎事件:大型 × 高YoY>20% × 非空頭
base = []
for sid, rows in rev.items():
    if sid == BENCH or sid not in C:
        continue
    if D.avgvol.get(sid, 0) < med:
        continue
    by = {r[1]: r[2] for r in rows if r[2]}
    for avail, ym, r in rows:
        if not r:
            continue
        prev = by.get(ym - 100)
        if not prev or prev <= 0:
            continue
        if r / prev - 1 < 0.2:
            continue
        ed = D.next_td(avail)
        if not ed or ed < cal[0] or ed > cal[-1]:
            continue
        if regime.get(ed) is not True:
            continue
        base.append((sid, ed))


def feat(sid, ed):
    i = cidx.get(ed)
    if i is None or i < 60 or i + HOLD >= len(cal):
        return None
    px = C[sid].get(ed)
    if not px or px <= 0:
        return None
    w = [C[sid][cal[k]] for k in range(i - 60, i + 1) if cal[k] in C[sid] and C[sid][cal[k]] > 0]
    if len(w) < 50:
        return None
    return dict(ma=px > statistics.mean(w), mom=px / w[0] - 1 > 0,
                brk=px >= 0.95 * max(w), inst=inst_buy(sid, ed))


def excess(sid, ed, cost=COST):
    i = cidx[ed]
    e, x = cal[i], cal[i + HOLD]
    if e not in EW or e not in C[sid] or x not in C[sid] or C[sid][e] <= 0:
        return None
    return (C[sid][x] / C[sid][e] - 1 - cost) - EW[e]


def summ(name, sub):
    gates.report(name, [(ed, excess(s, ed)) for s, ed in sub])


F = {(s, ed): feat(s, ed) for s, ed in base}
base = [(s, ed) for s, ed in base if F[(s, ed)]]
print("=" * 66)
print(f"基礎=大型×高YoY>20%×非空頭  n={len(base)}  基準=等權 持有{HOLD}日 成本{COST*100:.1f}%")
print(f"法人資料 t86 檔數 {N_T86}")
print("=" * 66)
summ("基礎(無濾)", base)
print("\n— 疊單一濾網 —")
summ("+站上MA60", [(s, ed) for s, ed in base if F[(s, ed)]['ma']])
summ("+動能60>0", [(s, ed) for s, ed in base if F[(s, ed)]['mom']])
summ("+近60新高", [(s, ed) for s, ed in base if F[(s, ed)]['brk']])
summ("+法人淨買", [(s, ed) for s, ed in base if F[(s, ed)]['inst']])
print("\n— 組合 —")
summ("MA60+動能", [(s, ed) for s, ed in base if F[(s, ed)]['ma'] and F[(s, ed)]['mom']])
summ("MA60+動能+新高", [(s, ed) for s, ed in base if F[(s, ed)]['ma'] and F[(s, ed)]['mom'] and F[(s, ed)]['brk']])
summ("MA60+動能+法人", [(s, ed) for s, ed in base if F[(s, ed)]['ma'] and F[(s, ed)]['mom'] and F[(s, ed)]['inst']])
summ("全開(MA+動能+新高+法人)", [(s, ed) for s, ed in base if all(F[(s, ed)][k] for k in ('ma', 'mom', 'brk', 'inst'))])
print("\n看:哪個組合把超額放大且勝率過50%、OOS不崩、n別太小(<100慎判)。")
