#!/usr/bin/env python3
"""
壓力測唯一活訊號:大型×高YoY×非空頭×站MA60×動能>0。
掃 持有天數 × YoY門檻 × 大小切點,看是否「整片都正」(穩健)還是只一組贏(擬合)。
2025 那欄 = 真·樣本外。基準=等權。
用法: python studies/revenue_stress.py
"""
import os
import sys
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from framework import config
from framework.data import Data, load_revenue

COST = config.COST
BENCH = config.BENCH
OOS2 = config.OOS2_FROM

D = Data()
cal, C, cidx, regime = D.cal, D.C, D.cidx, D.regime
rev = load_revenue()

# 預掃所有事件:(sid, ed, yoy, ma_ok, mom_ok, vol)
EVT = []
for sid, rows in rev.items():
    if sid == BENCH or sid not in C:
        continue
    by = {r[1]: r[2] for r in rows if r[2]}
    for avail, ym, r in rows:
        if not r:
            continue
        prev = by.get(ym - 100)
        if not prev or prev <= 0:
            continue
        yoy = r / prev - 1
        if yoy < 0.1:
            continue
        ed = D.next_td(avail)
        if not ed or ed < cal[0] or ed > cal[-1]:
            continue
        if regime.get(ed) is not True:
            continue
        i = cidx[ed]
        if i < 60 or i + 60 >= len(cal):
            continue
        px = C[sid].get(ed)
        if not px or px <= 0:
            continue
        w = [C[sid][cal[k]] for k in range(i - 60, i + 1) if cal[k] in C[sid] and C[sid][cal[k]] > 0]
        if len(w) < 50:
            continue
        EVT.append((sid, ed, yoy, px > statistics.mean(w), px / w[0] - 1 > 0, D.avgvol.get(sid, 0)))


def excess(sid, ed, h, cost=COST):
    ew = D.build_ew(h)
    i = cidx[ed]
    if i + h >= len(cal):
        return None
    e, x = cal[i], cal[i + h]
    if e not in ew or e not in C[sid] or x not in C[sid] or C[sid][e] <= 0:
        return None
    return (C[sid][x] / C[sid][e] - 1 - cost) - ew[e]


def stat(sub, h):
    rs = [(ed, excess(s, ed, h)) for s, ed in sub]
    rs = [(ed, v) for ed, v in rs if v is not None]
    if len(rs) < 30:
        return None
    v = [x for _, x in rs]
    y25 = [x for ed, x in rs if ed >= OOS2]
    return (len(v), statistics.mean(v) * 100, sum(1 for x in v if x > 0) / len(v) * 100,
            (statistics.mean(y25) * 100 if len(y25) >= 20 else None), len(y25))


print("=" * 70)
print("壓力掃描:大型×非空頭×站MA60×動能>0,掃 YoY門檻 × 持有天數(基準=等權)")
print("大型=量前50%。每格: 全期超額% / 勝率% / 2025超額%(真OOS)   n太小略")
print("=" * 70)
SIZE = D.size_pct(0.5)
for yt in (0.1, 0.2, 0.3, 0.5):
    sub = [(s, ed) for s, ed, y, ma, mo, vv in EVT if y >= yt and ma and mo and vv >= SIZE]
    line = f"YoY>{int(yt*100):>3}% (n={len(sub):>4}): "
    for h in (10, 20, 40, 60):
        r = stat(sub, h)
        line += (f" {h}日[{r[1]:+.2f}/{r[2]:.0f}%/{('%.2f' % r[3]) if r[3] is not None else '–'}]"
                 if r else f" {h}日[–]")
    print(line)

print("\n— 大小切點敏感(固定 YoY>20%, 持有20日)—")
for nm, p in (("全部(不分大小)", 0.0), ("中型↑(前70%)", 0.3), ("大型(前50%)", 0.5), ("最大(前20%)", 0.8)):
    th = D.size_pct(p)
    sub = [(s, ed) for s, ed, y, ma, mo, vv in EVT if y >= 0.2 and ma and mo and vv >= th]
    r = stat(sub, 20)
    print(f"  {nm:<14}: " + (f"n={r[0]:>4} 全期{r[1]:+.2f}% 勝率{r[2]:.0f}% "
          f"2025OOS{('%.2f' % r[3]) if r[3] is not None else '–'}(n{r[4]})" if r else "n太小"))
print("\n看:2025(真OOS)那欄若多為正 → 三年外都成立,訊號穩。負→2025破功,要重判。")
