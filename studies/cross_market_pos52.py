#!/usr/bin/env python3
"""
跨市場驗證:「逼近 52 週高」在美股是否也成立?

邏輯:一個因子若只在台股、只在特定樣本期成立 → 高度可疑(可能是該市場該時期的偶然)。
      若在另一個市場、獨立資料上重現同樣的**單調分位曲線** → 大幅提高可信度。

台股結果(2010-2026,164/686 個再平衡日):分位單調 +0.90/+0.92,
最高分位 +0.48~0.51%/20日(扣 0.6% 成本),最低分位 -1.16~-1.21%。

美股樣本說明(誠實揭露):清單取自 FinMind USStockInfo 的字母序前段,
含少量 ETF,**不是**精選大型股。字母序與動能無關 → 視為準隨機抽樣,
但流動性與代表性不如台股樣本,結論僅供佐證,不能當獨立確認。

用法: python studies/cross_market_pos52.py
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
ap.add_argument("--kline", default="us_kline.json")
ap.add_argument("--bench", default="SPY")
ap.add_argument("--rebalance", type=int, default=21)
ap.add_argument("--is-end", default=config.OOS2_FROM)
ap.add_argument("--n-random", type=int, default=100)
ap.add_argument("--cost", type=float, default=0.002, help="美股來回成本(免佣,含價差滑價)")
args = ap.parse_args()

path = os.path.join(config.DATA_DIR, args.kline)
print("=" * 80)
print("跨市場驗證:逼近 52 週高 @ 美股")
print("=" * 80)

data = Data(kline_path=path, bench=args.bench)
print(f"資料 {data.cal[0]}~{data.cal[-1]}  {len(data.d)}檔  基準 {args.bench}  "
      f"分割/公司行為斷點 {data.split_adjusted}")
print(f"來回成本設 {args.cost*100:.1f}%(美股免佣,主要是價差;台股用 0.6%)")

panel = S.build_panel(data, revenue=None, inst=None,
                      rebalance=args.rebalance, is_end=None)
dates, f = panel["dates"], panel["feat"]
pos = f["pos52"]
is_m = dates < args.is_end
print(f"面板 {len(dates)} 列  樣本內 {int(is_m.sum())} / 樣本外 {int((~is_m).sum())}")

# ---- 分位曲線 ----
print("\n" + "=" * 80)
print(f"52週位置 10 分位(0=最接近年低 … 9=最接近年高),持有20日,樣本內")
print("=" * 80)
qb = np.full(len(pos), -1, int)
for dt in np.unique(dates[is_m]):
    idx = np.flatnonzero((dates == dt) & np.isfinite(pos) & is_m)
    if len(idx) < 30:
        continue
    order = np.argsort(pos[idx])
    r = np.empty(len(idx))
    r[order] = np.arange(len(idx))
    qb[idx] = np.minimum((r / len(idx) * 10).astype(int), 9)

ex20 = S.peer_excess(panel, 20, cost=args.cost)
curve = []
print(f"  {'分位':<6}{'n':>8}{'超額/20日':>12}{'勝率':>8}")
for b in range(10):
    idx = np.flatnonzero((qb == b) & np.isfinite(ex20))
    if len(idx) < 30:
        continue
    v = ex20[idx]
    curve.append(v.mean())
    print(f"  {b:<6}{len(idx):>8}{v.mean()*100:>11.2f}%{(v > 0).mean()*100:>7.0f}%")
if len(curve) >= 5:
    r = np.corrcoef(np.arange(len(curve)), curve)[0, 1]
    print(f"  單調性相關 = {r:+.2f}   (台股:+0.90 月 / +0.92 週)")
    print(f"  最高−最低 = {(curve[-1]-curve[0])*100:+.2f}%/20日   (台股:+1.67~+1.70%)")

# ---- 多持有期 ----
print("\n多持有期(前10%,樣本內,標準化每20日)")
top = qb == 9
for h in panel["holds"]:
    ex = S.peer_excess(panel, h, cost=args.cost)
    idx = np.flatnonzero(top & np.isfinite(ex))
    if len(idx) >= 30:
        print(f"  持有{h:>3}日: {ex[idx].mean()*20/h*100:+.2f}%  n={len(idx)}")

# ---- 校準關卡 ----
print("\n" + "=" * 80)
print("校準關卡 — 樣本內(前10%,持有20日)")
print("=" * 80)
C.report(C.calibrate(panel, top & is_m, 20, ex=ex20, n_random=args.n_random), indent="  ")

print("\n" + "=" * 80)
print("判讀:若美股分位曲線同樣單調向上 → 因子在另一市場、另一套資料上重現,")
print("      台股結果不是單一市場的偶然。若不單調 → 台股結果可疑。")
print("=" * 80)
