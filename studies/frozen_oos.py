#!/usr/bin/env python3
"""
凍結候選 → 開樣本外(只能開一次)。

規矩(自己對自己誠實的唯一辦法):
  1. 條件寫死在下面 FROZEN,**跑之前不准再回頭改**。
  2. 跑完不管好壞,結論就是結論。想改條件 → 那叫重新搜尋,結果作廢,
     且 OOS 已被你看過 → 它不再是乾淨樣本外。
  3. 樣本外 = OOS_FROM 之後(預設 2025-01-01,含 2026 上半年)。

用法:
  python studies/frozen_oos.py                 # 用下面 FROZEN
  python studies/frozen_oos.py --oos-from 2025-01-01
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
from framework import gates

# ============ 凍結的候選(改這裡 = 重新搜尋,不是驗證)============
FROZEN = {
    "name": "大型 × 非空頭 × 站上MA120 × 252日動能>0 × 低波 × 營收YoY>20%",
    "labels": {
        "size": "大型(量前50%)",
        "regime": "只非空頭",
        "trend": "站上MA120",
        "mom": "252日動能>0",
        "vol": "低波(<中位)",
        "rev": "營收YoY>20%",
        "inst": "不管",
    },
    "hold": 60,
}

ap = argparse.ArgumentParser()
ap.add_argument("--oos-from", default=config.OOS2_FROM)
ap.add_argument("--no-inst", action="store_true")
args = ap.parse_args()

print("=" * 76)
print("凍結候選 → 樣本外驗證(一次性)")
print("=" * 76)
print(f"候選:{FROZEN['name']}  [持有{FROZEN['hold']}日]")
print(f"樣本外:{args.oos_from} 起")

data = Data()
rev = load_revenue()
inst = None if args.no_inst else load_t86()

# 建「全期」面板(不設 is_end),再用日期切 IS / OOS
panel = S.build_panel(data, revenue=rev, inst=inst, is_end=None)
preds = S.build_predicates(panel)

# 組出凍結遮罩
masks = []
for dim, lab in FROZEN["labels"].items():
    m = dict(preds[dim])[lab]
    if m is not None:
        masks.append(m)
base = np.logical_and.reduce(masks) if masks else np.ones(len(panel["dates"]), bool)

is_mask = base & (panel["dates"] < args.oos_from)
oos_mask = base & (panel["dates"] >= args.oos_from)
h = FROZEN["hold"]

print(f"\n面板 {len(panel['dates'])} 列  命中 {int(base.sum())} 列 "
      f"(IS {int(is_mask.sum())} / OOS {int(oos_mask.sum())})")


def show(tag, mask):
    r = S.evaluate(panel, mask, h, min_n=30, min_dates=3)
    if not r:
        print(f"  {tag}: 樣本不足")
        return None
    score, n, win, nd = r
    print(f"  {tag}: 每20日標準化超額 {score*100:+.2f}%  勝率 {win:.0f}%  n={n}  日數={nd}")
    return r


print("\n— 結果 —")
r_is = show("樣本內(搜尋用,已知會好看)", is_mask)
r_oos = show("🔓 樣本外(這一刀才算數)  ", oos_mask)

# 樣本外的逐年拆解 + bootstrap
if r_oos:
    idx = np.flatnonzero(oos_mask)
    ex = (panel["fwd"][h][idx] - config.COST) - panel["ew"][h][idx]
    ex = ex * 20.0 / h
    pairs = list(zip(panel["dates"][idx].tolist(), ex.tolist()))
    print("\n— 樣本外細節 —")
    gates.report("OOS", pairs, oos_from="9999")   # 不再切,只看分年
    gates.report_boot("OOS bootstrap", [v for _, v in pairs])

print("\n" + "=" * 76)
if r_is and r_oos:
    keep = r_oos[0] > 0 and r_oos[0] > r_is[0] * 0.3
    print("判決:" + ("🟢 樣本外仍為正且沒衰減太多 → 候選存活,可進實盤前的下一步"
                    if keep else
                    "🔴 樣本外崩掉 → 樣本內那個 +% 是擬合/運氣,判死。"))
print("提醒:這一刀已用掉。要再測別的條件,得用新的、從未看過的資料。")
print("=" * 76)
