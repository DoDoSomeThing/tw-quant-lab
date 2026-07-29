#!/usr/bin/env python3
"""
回推搜尋:掃大量條件組合,找「可能有勝率」的訊號 —— 並用第六關擋住 data snooping。

只在 **樣本內(IS,預設 <2025-01-01)** 搜尋。2025-2026 鎖起來不碰,
等你凍結一組候選後,才用 studies/frozen_oos.py 開一次(不准回頭改)。

流程:
  1. 建面板(每個 再平衡日×股票 一列,含 point-in-time 特徵 + 前向報酬)
  2. 全網格掃描 → IS 排行榜
  3. **Best-of-N 置換檢定** — 同一套搜尋跑在打亂資料上 N 次,
     得到「純運氣最好能掃到多好」的分布。真實最佳沒贏過 → 噪音。
  4. 鄰居穩定度 — 最佳組的每一維單獨換掉,鄰居也正才算穩(孤峰=擬合)

用法:
  python studies/reverse_search.py                 # 預設 30 次置換
  python studies/reverse_search.py --trials 50
  python studies/reverse_search.py --no-inst       # 跳過法人(載入較快)
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

from framework import config
from framework.data import Data, load_revenue, load_t86
from framework import search as S

ap = argparse.ArgumentParser()
ap.add_argument("--trials", type=int, default=30, help="置換檢定次數")
ap.add_argument("--is-end", default=config.OOS2_FROM, help="樣本內結束日(此日起鎖住)")
ap.add_argument("--no-inst", action="store_true", help="不載入法人資料")
ap.add_argument("--top", type=int, default=15)
ap.add_argument("--max-active", type=int, default=4, help="一組最多幾個生效條件")
ap.add_argument("--narrow", action="store_true", help="只用原本 7 個核心維度")
args = ap.parse_args()

print("=" * 76)
print("回推搜尋(只在樣本內;OOS 鎖住不碰)")
print("=" * 76)

data = Data()
print(f"資料 {data.cal[0]}~{data.cal[-1]}  {len(data.d)}檔  "
      f"後復權抹平 {data.split_adjusted} 個公司行為斷點")
print(f"樣本內 = date < {args.is_end}   |   🔒 {args.is_end} 之後鎖住,本腳本不碰")

rev = load_revenue()
inst = None if args.no_inst else load_t86()
print(f"營收 {len(rev)} 檔" + ("" if inst is None else f"  法人 {len(inst)} 檔"))

print("\n建面板中…")
panel = S.build_panel(data, revenue=rev, inst=inst, is_end=args.is_end)
print(f"  面板 {len(panel['dates'])} 列  再平衡日 {panel['n_dates']} 個  "
      f"持有期 {panel['holds']}")

preds = S.build_predicates(panel, wide=not args.narrow)
combos = S.iter_combos(preds, args.max_active)
print(f"  維度 {len(preds)} 個:{', '.join(preds.keys())}")
print(f"  搜尋空間:{len(combos)} 組條件(最多 {args.max_active} 個生效)× "
      f"{len(panel['holds'])} 種持有期 = {len(combos)*len(panel['holds'])} 次評估")

print("\n掃描中…")
res = S.scan(panel, preds, combos=combos)
if not res:
    raise SystemExit("無有效組合(樣本不足)。")
print(f"  通過樣本門檻(n>=100、日數>=10)的組合:{len(res)}")

print(f"\n— IS 排行榜 Top {args.top}(score = 每20日標準化超額 vs 全市場等權,已扣成本)—")
for k, r in enumerate(res[:args.top], 1):
    print(f"  {k:>2}. {r['score']*100:+6.2f}%  勝率{r['win']:.0f}%  n={r['n']:<5} "
          f"日數{r['n_dates']:<3} | {S.describe(r)}")

# 幼稚的「顯著」計數(用來看多重檢定有多毒)
pos = sum(1 for r in res if r["score"] > 0)
print(f"\n  帳面為正的組合:{pos}/{len(res)} ({pos/len(res)*100:.0f}%)")

best = res[0]
print("\n" + "=" * 76)
print(f"第六關:Best-of-N 置換檢定({args.trials} 次)")
print("  問的不是「這組好不好」,是「亂掃也能掃到這麼好嗎」")
print("=" * 76)
p, nulls = S.best_of_n_test(panel, preds, best["score"], trials=args.trials, combos=combos)

print(f"\n  真實最佳      : {best['score']*100:+.2f}%   {S.describe(best)}")
print(f"  亂掃最佳(中位): {np.median(nulls)*100:+.2f}%   "
      f"範圍 [{nulls.min()*100:+.2f}%, {nulls.max()*100:+.2f}%]")
print(f"  p 值(亂掃 >= 真實的比例)= {p:.3f}")
if p > 0.05:
    print("  🔴 判決:贏不過「亂掃的最好成績」→ 這個排行榜第一名是噪音,不是 edge。")
else:
    print("  🟢 判決:超出純運氣能掃到的範圍 → 值得進下一關(凍結後跑 OOS)。")

print("\n" + "=" * 76)
print("第七關:基準假象 + 集中度/聚類")
print("  置換檢定擋不住『同一批股票重複中選』(一次產業押注會被判成真訊號)")
print("=" * 76)
def mask_of(item):
    ms = [m for m in (dict(preds[d])[l] for d, l in item["labels"].items()) if m is not None]
    return np.logical_and.reduce(ms) if ms else np.ones(len(panel["dates"]), bool)


survivors = []
peer_cache = {}
for rank, cand in enumerate(res[:5], 1):
    h = cand["hold"]
    if h not in peer_cache:
        peer_cache[h] = S.peer_excess(panel, h)
    print(f"\n  #{rank} {S.describe(cand)}")
    print(f"     vs 全市場等權:{cand['score']*100:+.2f}%/20日")
    ok = S.report_concentration(S.concentration(panel, mask_of(cand), h, ex=peer_cache[h]),
                                indent="     ")
    if ok:
        survivors.append(cand)
print(f"\n  Top5 通過第七關:{len(survivors)}/5")
best_mask = mask_of(best)

print("\n— 鄰居穩定度(最佳組每一維單獨換掉;孤峰=擬合)—")
nb = S.neighbors(panel, preds, best)
good = sum(1 for _, s, _ in nb if s is not None and s > 0)
tot = sum(1 for _, s, _ in nb if s is not None)
for desc, s, n in nb:
    print(f"  {desc:<34} " + (f"{s*100:+6.2f}%  n={n}" if s is not None else "樣本不足"))
print(f"  鄰居為正 {good}/{tot}" +
      ("  → 穩健" if tot and good / tot >= 0.7 else "  → 不穩,像孤峰"))

print("\n" + "=" * 76)
print("提醒:以上全部是樣本內。就算第六關過了,也只代表「值得凍結後測 OOS」,")
print(f"不代表有 edge。OOS({args.is_end} 之後)只能開一次,開之前不准回頭改條件。")
print("=" * 76)
