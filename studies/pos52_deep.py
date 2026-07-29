#!/usr/bin/env python3
"""
深度檢驗唯一淨正的純價格訊號:**逼近 52 週高**。

背景:16 年(2010-2026)、164 個再平衡日、校準關卡下,12 個純價格訊號只有它
淨超額為正(+0.51%/20日,已扣 0.6% 來回成本),且拿掉最好 3 天仍 +0.41%。
其餘「抄底類」(KD超賣/RSI最低/52週低/乖離最低/短期反轉)全部比隨機還爛。

本腳本把它壓到底:
  1. 分位曲線(是否單調 —— 越接近 52 週高越好?)
  2. 多持有期(10/20/40/60 日)一致性
  3. 成本敏感(0.2% ~ 1.2% 來回)
  4. 分年(16 年逐年,含 2008 後各種市況)
  5. 選股比例敏感(前 5% / 10% / 20% / 30%)
  6. 校準關卡(同規模隨機對照)
  7. **樣本外**:2025-01 起(搜尋階段從未使用)

所有門檻皆由隨機對照分布決定,無人為設定。
用法: python studies/pos52_deep.py
"""
import os
import sys
import argparse
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore")

import numpy as np

from framework import config
from framework.data import Data
from framework import search as S
from framework import calibrate as C

ap = argparse.ArgumentParser()
ap.add_argument("--is-end", default=config.OOS2_FROM)
ap.add_argument("--n-random", type=int, default=100)
ap.add_argument("--rebalance", type=int, default=21)
args = ap.parse_args()

print("=" * 80)
print("深度檢驗:逼近 52 週高(唯一淨正的純價格訊號)")
print("=" * 80)

data = Data()
# 全期面板(不切 is_end),之後用日期切 IS / OOS
panel = S.build_panel(data, revenue=None, inst=None, rebalance=args.rebalance, is_end=None)
dates, f = panel["dates"], panel["feat"]
pos = f["pos52"]
is_m = dates < args.is_end
oos_m = dates >= args.is_end
print(f"資料 {data.cal[0]}~{data.cal[-1]}  {len(data.d)}檔  面板 {len(dates)} 列")
print(f"樣本內 {int(is_m.sum())} 列 / 樣本外 {int(oos_m.sum())} 列(切點 {args.is_end})")


def top_mask(pct, base=None):
    """每個再平衡日取 pos52 前 pct 比例。base 限制在某個日期子集。"""
    m = np.zeros(len(dates), bool)
    sub = np.ones(len(dates), bool) if base is None else base
    for dt in np.unique(dates[sub]):
        idx = np.flatnonzero((dates == dt) & np.isfinite(pos) & sub)
        if len(idx) < 50:
            continue
        k = max(1, int(len(idx) * pct))
        m[idx[np.argsort(-pos[idx])[:k]]] = True
    return m


def ex_of(h, cost=config.COST):
    return S.peer_excess(panel, h, cost=cost)


def summ(mask, h, cost=config.COST, sub=None):
    ex = ex_of(h, cost)
    mm = mask if sub is None else (mask & sub)
    idx = np.flatnonzero(mm & np.isfinite(ex))
    if len(idx) < 30:
        return None
    v = ex[idx]
    return v.mean() * 20.0 / h, len(idx), (v > 0).mean() * 100, len(np.unique(dates[idx]))


# ---- 1. 分位曲線 ----
print("\n" + "=" * 80)
print("1) 52週位置 10 分位(0=最接近年低 … 9=最接近年高),持有20日,樣本內")
print("=" * 80)
qb = np.full(len(pos), -1, int)
for dt in np.unique(dates[is_m]):
    idx = np.flatnonzero((dates == dt) & np.isfinite(pos) & is_m)
    if len(idx) < 50:
        continue
    order = np.argsort(pos[idx])
    r = np.empty(len(idx))
    r[order] = np.arange(len(idx))
    qb[idx] = np.minimum((r / len(idx) * 10).astype(int), 9)
curve = []
print(f"  {'分位':<6}{'n':>8}{'超額/20日':>12}{'勝率':>8}")
for b in range(10):
    s = summ(qb == b, 20)
    if s:
        curve.append(s[0])
        print(f"  {b:<6}{s[1]:>8}{s[0]*100:>11.2f}%{s[2]:>7.0f}%")
if len(curve) >= 5:
    r = np.corrcoef(np.arange(len(curve)), curve)[0, 1]
    print(f"  單調性相關 = {r:+.2f}")

# ---- 2. 多持有期 ----
print("\n" + "=" * 80)
print("2) 多持有期一致性(前10%,樣本內,標準化每20日)")
print("=" * 80)
m10 = top_mask(0.10, base=is_m)
for h in panel["holds"]:
    s = summ(m10, h, sub=is_m)
    print(f"  持有{h:>3}日: " + (f"{s[0]*100:+.2f}%  n={s[1]}  勝率{s[2]:.0f}%" if s else "樣本不足"))

# ---- 3. 成本敏感 ----
print("\n" + "=" * 80)
print("3) 成本敏感(前10%,持有20日,樣本內)")
print("=" * 80)
for c_ in (0.002, 0.004, 0.006, 0.008, 0.012):
    s = summ(m10, 20, cost=c_, sub=is_m)
    print(f"  來回{c_*100:.1f}%: " + (f"{s[0]*100:+.2f}%" if s else "—"))

# ---- 4. 分年 ----
print("\n" + "=" * 80)
print("4) 逐年(前10%,持有20日,全期含樣本外)")
print("=" * 80)
ex20 = ex_of(20)
idx = np.flatnonzero(top_mask(0.10) & np.isfinite(ex20))
yrs = np.array([d[:4] for d in dates[idx]])
for y in sorted(set(yrs.tolist())):
    v = ex20[idx][yrs == y]
    tag = " (樣本外)" if y >= args.is_end[:4] else ""
    print(f"  {y}: {v.mean()*100:+6.2f}%  n={len(v):>5}  勝率{(v>0).mean()*100:>3.0f}%{tag}")

# ---- 5. 選股比例 ----
print("\n" + "=" * 80)
print("5) 選股比例敏感(持有20日,樣本內)")
print("=" * 80)
for p_ in (0.05, 0.10, 0.20, 0.30):
    s = summ(top_mask(p_, base=is_m), 20, sub=is_m)
    print(f"  前{p_*100:>4.0f}%: " + (f"{s[0]*100:+.2f}%  n={s[1]}" if s else "—"))

# ---- 6. 校準關卡(樣本內) ----
print("\n" + "=" * 80)
print("6) 校準關卡 — 樣本內(前10%,持有20日)")
print("=" * 80)
c_is = C.calibrate(panel, m10 & is_m, 20, n_random=args.n_random)
C.report(c_is, indent="  ")

# ---- 7. 樣本外 ----
print("\n" + "=" * 80)
print(f"7) 🔓 樣本外({args.is_end} 起,搜尋階段從未使用)")
print("=" * 80)
m_oos = top_mask(0.10, base=oos_m)
c_oos = C.calibrate(panel, m_oos & oos_m, 20, n_random=args.n_random)
C.report(c_oos, indent="  ")
for h in panel["holds"]:
    s = summ(m_oos, h, sub=oos_m)
    print(f"  持有{h:>3}日: " + (f"{s[0]*100:+.2f}%  n={s[1]}" if s else "樣本不足"))

print("\n" + "=" * 80)
if c_is and c_oos:
    print(f"樣本內 {c_is['metrics']['excess20']['real']*100:+.2f}%/20日 → "
          f"樣本外 {c_oos['metrics']['excess20']['real']*100:+.2f}%/20日"
          f"(樣本外判定:{'通過' if c_oos['passed'] else '未通過'})")
print("=" * 80)
