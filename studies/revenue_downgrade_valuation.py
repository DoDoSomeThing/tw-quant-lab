#!/usr/bin/env python3
"""
Phase 2 —— 「便宜是不是買進理由」直球驗證。

Phase 1 已定案(results/營收連續下修_2026-08-06.md):連續下修 = 負面訊號,
A 組(streak>=3)超額百分位 0.0,樣本內外方向一致。

本腳本回答 SPEC §0 登記的第二個問題:把 A 組**再按估值切兩半**,
  便宜半邊「更差或持平」→ 便宜不提供保護,「便宜」不是買進理由(價值陷阱)
  便宜半邊「明顯較好」  → 估值有緩衝作用,值得續查

切估值用 **PB 不用 PE**(SPEC §6):虧損股 PE 是 "-" 大量缺值,且分母趨零會爆。
切法是**每個再平衡日在 A 組內取橫斷面中位數**(不是全期固定門檻)——
否則會被 PB 的長期水準漂移污染,變成在比年代而不是比貴賤。

⚠️ TWSE BWIBBU_d 只含**上市**,上櫃股票沒有估值資料會被丟掉 → 樣本會縮,
   腳本會印出覆蓋率,結論必須註明樣本僅含上市。

用法:
  export QLAB_KLINE_FILE=kline_deep_long.json
  export QLAB_REVENUE_FILE=revenue_long.json
  python backfill/backfill_valuation.py          # 先抓估值
  python studies/revenue_downgrade_valuation.py  # 樣本內
  python studies/revenue_downgrade_valuation.py --is-end 9999 --is-start 2025-01-01  # OOS
"""
import os
import sys
import json
import glob
import argparse
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore", category=FutureWarning)

import numpy as np

from framework import config
from framework.data import Data, load_revenue
from framework import search as S
from framework import calibrate as CAL
from framework import gates

ap = argparse.ArgumentParser()
ap.add_argument("--is-end", default=config.OOS2_FROM)
ap.add_argument("--is-start", default=None)
ap.add_argument("--oos-from", default="9999")
ap.add_argument("--rebalance", type=int, default=21)
ap.add_argument("--min-streak", type=int, default=3)
ap.add_argument("--n-random", type=int, default=200)
ap.add_argument("--val-dir", default=os.path.join(config.DATA_DIR, "valuation"))
args = ap.parse_args()

print("=" * 78)
print("Phase 2 — 下修中的股票,便宜的那一半有沒有比較不慘?")
print("=" * 78)

data = Data()
revenue = load_revenue()


# ---------- 與 Phase 1 完全相同的 streak 定義(不可改)----------
def yoy_streaks(rows):
    by_ym = {r[1]: r[2] for r in rows if r[2]}
    seq = []
    for avail, ym, rev in sorted(rows, key=lambda x: x[1]):
        if not rev:
            continue
        prev = by_ym.get(ym - 100)
        if not prev or prev <= 0:
            continue
        seq.append((avail, ym, rev / prev - 1))
    avails, yms, yoys, dn, up = [], [], [], [], []
    d = u = 0
    for i, (avail, ym, y) in enumerate(seq):
        if i > 0:
            prev_ym, prev_y = yms[-1], yoys[-1]
            expect = prev_ym + 1 if prev_ym % 100 < 12 else prev_ym + 89
            if ym != expect:
                d = u = 0
            elif y < prev_y:
                d, u = d + 1, 0
            elif y > prev_y:
                d, u = 0, u + 1
            else:
                d = u = 0
        avails.append(avail); yms.append(ym); yoys.append(y)
        dn.append(d); up.append(u)
    if not avails:
        return None
    o = np.argsort(np.array(avails))
    return (np.array(avails)[o], np.array(yoys)[o],
            np.array(dn)[o], np.array(up)[o])


REV = {sid: r for sid, r in ((s, yoy_streaks(rows)) for s, rows in revenue.items())
       if r is not None}

panel = S.build_panel(data, revenue=revenue, inst=None,
                      rebalance=args.rebalance, is_end=args.is_end)
dates, sids = panel["dates"], panel["sid"]
yoy = panel["feat"]["yoy"]

PER = (dates >= args.is_start) if args.is_start else np.ones(len(dates), dtype=bool)

dn_arr = np.full(len(dates), np.nan)
for sid in np.unique(sids):
    s = REV.get(sid)
    if s is None:
        continue
    m = np.flatnonzero(sids == sid)
    pos = np.searchsorted(s[0], dates[m], side="right") - 1
    ok = pos >= 0
    dn_arr[m[ok]] = s[2][pos[ok]]

K = args.min_streak
A = (dn_arr >= K) & PER

# ---------- 載入估值,對齊面板每一列 ----------
files = sorted(glob.glob(os.path.join(args.val_dir, "*.json")))
VAL = {}
for f in files:
    ymd = os.path.basename(f)[:8]
    dt = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
    with open(f, encoding="utf-8") as fh:
        VAL[dt] = json.load(fh)
print(f"\n估值檔:{len(VAL)} 天  {min(VAL) if VAL else '-'} ~ {max(VAL) if VAL else '-'}")

pb_arr = np.full(len(dates), np.nan)
for i in range(len(dates)):
    rec = VAL.get(dates[i])
    if not rec:
        continue
    v = rec.get(sids[i])
    if v and v.get("pb") is not None and v["pb"] > 0:
        pb_arr[i] = v["pb"]

n_A = int(A.sum())
n_A_pb = int((A & np.isfinite(pb_arr)).sum())
print(f"A 組(下修≥{K}月):{n_A} 列,其中有 PB 的 {n_A_pb} 列 "
      f"({n_A_pb/max(n_A,1)*100:.0f}%) ← 缺的是上櫃/當日無估值")
print("⚠️ TWSE BWIBBU_d 只含上市 → 以下結論樣本僅含上市股")

# ---------- 每個再平衡日,在 A 組內取 PB 橫斷面中位數切兩半 ----------
cheap = np.zeros(len(dates), dtype=bool)
rich = np.zeros(len(dates), dtype=bool)
pbq = np.full(len(dates), -1, dtype=int)      # A 組內 PB 五分位(補充用)
for dt in np.unique(dates[A]):
    m = A & (dates == dt) & np.isfinite(pb_arr)
    k = int(m.sum())
    if k < 10:                                 # 當日 A 組太少,不切
        continue
    idx = np.flatnonzero(m)
    med = np.median(pb_arr[idx])
    cheap[idx[pb_arr[idx] <= med]] = True
    rich[idx[pb_arr[idx] > med]] = True
    order = np.argsort(pb_arr[idx])
    ranks = np.empty(k, dtype=float)
    ranks[order] = np.arange(k)
    pbq[idx] = np.minimum((ranks / k * 5).astype(int), 4)


def line(lab, mask, ex, k):
    v = ex[mask & np.isfinite(ex)]
    if len(v) < 30:
        print(f"  {lab:<26}{'樣本不足':>10}  n={len(v)}")
        return None
    ss = sids[mask & np.isfinite(ex)]
    uniq = np.unique(ss)
    per = np.array([v[ss == s].mean() for s in uniq])
    print(f"  {lab:<26}{v.mean()*k*100:>8.2f}%{np.median(v)*k*100:>9.2f}%"
          f"{(v > 0).mean()*100:>7.0f}%{np.median(per)*k*100:>9.2f}%"
          f"{len(v):>8}{len(uniq):>7}")
    return v.mean() * k


for h in (20, 60):
    ex = S.peer_excess(panel, h)
    k = 20.0 / h
    print("\n" + "=" * 78)
    print(f"持有 {h} 日 — A 組內按 PB 切(每日橫斷面中位數),扣 {config.COST*100:.1f}% 成本")
    print("=" * 78)
    print(f"  {'組別':<26}{'平均':>9}{'中位':>9}{'勝率':>7}{'每檔中位':>9}"
          f"{'列數':>8}{'檔數':>7}")
    a_all = line(f"A 全體(有PB)", A & np.isfinite(pb_arr), ex, k)
    c = line("  ├ 便宜半邊(低PB)", cheap, ex, k)
    r = line("  └ 貴半邊(高PB)", rich, ex, k)
    if c is not None and r is not None:
        d = c - r
        print(f"  → 便宜 − 貴 = {d*100:+.2f}%/20日"
              + ("  便宜更慘(價值陷阱)" if d < 0 else "  便宜較不慘(有緩衝)"))
    print("  — 補充:A 組內 PB 五分位(0=最便宜)—")
    for q in range(5):
        line(f"  PB 分位 {q}", pbq == q, ex, k)

# ---------- 校準判定 ----------
H = 60
ex60 = S.peer_excess(panel, H)
print("\n" + "=" * 78)
print(f"校準判定 — 持有 {H} 日,對照 {args.n_random} 組同規模隨機選股")
print("=" * 78)
for lab, m in [("A 便宜半邊(低PB)", cheap), ("A 貴半邊(高PB)", rich)]:
    print(f"\n【{lab}】")
    CAL.report(CAL.calibrate(panel, m, H, ex=ex60, n_random=args.n_random))

print("\n" + "=" * 78)
print(f"便宜半邊 × 持有 {H} 日 — 分年")
print("=" * 78)
mC = cheap & np.isfinite(ex60)
gates.report("A便宜半邊", list(zip(dates[mC], ex60[mC])), oos_from=args.oos_from)
gates.report_boot("bootstrap", list(ex60[mC]))

print("\n" + "=" * 78)
print("判讀(SPEC §0 事前登記,跑完不准改):")
print("  便宜半邊 更差或持平 → 便宜不提供保護,「便宜」不是買進理由")
print("  便宜半邊 明顯較好   → 估值有緩衝作用,值得續查")
print("  ⚠️ 樣本僅含上市(TWSE BWIBBU_d 無上櫃)")
print("=" * 78)
