#!/usr/bin/env python3
"""
用「校準式判定」重新檢視先前被人訂門檻判死的訊號。

先前第七關的門檻(前5檔≥40%、日期<30、拿掉3天腰斬)是人訂的。
本腳本把同樣的訊號丟進 framework/calibrate.py —— 所有界線改由
「同規模、同日期結構的隨機選股」實測分布決定,人不填任何數字。

若先前判死的訊號在校準後變成「不比隨機更集中」,代表人訂門檻造成假陰性。
用法: python studies/recheck_calibrated.py [--n-random 200]
"""
import os
import sys
import argparse
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore", category=FutureWarning)

import numpy as np

from framework import config
from framework.data import Data, load_revenue, load_t86
from framework import search as S
from framework import calibrate as C

ap = argparse.ArgumentParser()
ap.add_argument("--n-random", type=int, default=200)
ap.add_argument("--is-end", default=config.OOS2_FROM)
args = ap.parse_args()

print("=" * 82)
print("校準式重檢:先前被『人訂門檻』判死的訊號,改用隨機對照分布重判")
print("=" * 82)
print(C.__doc__.split("── 事前登記的判定規則")[1].split("──────")[0].strip())
print()

data = Data()
panel = S.build_panel(data, revenue=load_revenue(), inst=load_t86(), is_end=args.is_end)
preds = S.build_predicates(panel, wide=True)
f, dates = panel["feat"], panel["dates"]
print(f"面板 {len(dates)} 列  樣本內 <{args.is_end}\n")


def m_of(labels):
    ms = [dict(preds[d])[l] for d, l in labels.items()]
    ms = [m for m in ms if m is not None]
    return np.logical_and.reduce(ms) if ms else np.ones(len(dates), bool)


# 營收 YoY 橫斷面最高分位
yoy = f["yoy"]
qb = np.full(len(yoy), -1, dtype=int)
for dt in np.unique(dates):
    m = (dates == dt) & np.isfinite(yoy)
    if m.sum() < 50:
        continue
    idx = np.flatnonzero(m)
    order = np.argsort(yoy[idx])
    ranks = np.empty(len(idx))
    ranks[order] = np.arange(len(idx))
    qb[idx] = np.minimum((ranks / len(idx) * 10).astype(int), 9)

CASES = [
    ("窄搜尋最佳(先前判死:前5檔51%)",
     m_of({"size": "大型(量前50%)", "regime": "只非空頭", "trend": "站上MA120",
           "mom": "252日動能>0", "vol": "低波(<中位)", "rev": "營收YoY>20%"}), 60),
    ("寬搜尋最佳(先前判死:前5檔86%)",
     m_of({"trend": "站上MA60", "mom": "252日動能>0", "rev": "營收YoY>20%",
           "bias": "乖離MA20<-5%"}), 10),
    ("寬搜尋#5(先前判死:前5檔43%)",
     m_of({"regime": "只非空頭", "vol": "低波(<中位)", "rev": "營收YoY>20%",
           "bias": "乖離MA20>5%"}), 10),
    ("營收YoY 最高分位(先前:無法判定)", qb == 9, 20),
    ("營收YoY 最低分位(對照組,預期為負)", qb == 0, 20),
    ("全市場(對照組,預期=0)", np.ones(len(dates), bool), 20),
]

results = []
for name, mask, h in CASES:
    print("=" * 82)
    print(f"{name}   [持有{h}日]")
    print("=" * 82)
    ex = S.peer_excess(panel, h)
    c = C.calibrate(panel, mask, h, ex=ex, n_random=args.n_random)
    ok = C.report(c, indent="  ")
    results.append((name, c, ok))
    print()

print("=" * 82)
print("總表")
print("=" * 82)
print(f"  {'訊號':<34}{'超額/20日':>11}{'百分位':>8}{'前5檔%':>9}{'(隨機中位)':>11}{'判定':>8}")
for name, c, ok in results:
    if not c:
        print(f"  {name:<34}  樣本不足")
        continue
    m = c["metrics"]
    print(f"  {name:<34}{m['excess20']['real']*100:>10.2f}%{m['excess20']['pct']:>8.1f}"
          f"{m['top5_share']['real']:>8.0f}%{m['top5_share']['null_med']:>10.0f}%"
          f"{'  通過' if ok else '  未通過':>8}")
print("\n說明:所有界線來自隨機對照分布,無人為設定。『百分位』= 真實值在 200 組")
print("      同規模隨機選股中的位置(>=95 才算效果量顯著)。")
