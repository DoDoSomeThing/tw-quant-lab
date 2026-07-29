#!/usr/bin/env python3
"""
驗證器的自我驗證 —— 陽性對照(positive control)。

動機:一個「什麼都判死」的驗證器,跟一個「什麼都放行」的一樣沒用。
如果我的關卡把**真的 edge** 也殺掉(假陰性),那所有判死結論都不可信。
所以必須測:**已知強度的真 edge,我的關卡抓不抓得到?**

做法:
  1. 用一條「無技術含量」的選股規則(對 sid+date 做雜湊 → 偽隨機分數,每日取前10%)。
     這條規則本身**沒有任何預測力**。
  2. 人工把已知強度 E 的超額**注入**這些被選中列的前向報酬(E = 每20日 x%)。
     現在這條規則就**確實擁有** E 這麼大的 edge —— 這是地面真相。
  3. 把這條規則丟進完整關卡(搜尋 + 第六關置換 + 第七關集中度),看判決。
  4. 掃不同 E,找出**偵測門檻**:我的關卡最小能抓到多大的 edge。

判讀:
  - E=0 應該被判死(否則就是假陽性,關卡太鬆)
  - E 夠大時應該被判活;若連 E 很大都判死 → **關卡有病,要修**
  - 偵測門檻若落在合理範圍(每20日 0.5~1%),代表關卡靈敏度 OK,
    那麼真實訊號被判死就是**市場真的沒有那麼大的 edge**,不是我在搞。

注意:注入會同時抬高 peer/等權基準(被選中的也在母體裡),
所以量到的效果約為 E×(1−選股比例)。報告已標示。

用法:
  python studies/validator_selftest.py
  python studies/validator_selftest.py --effects 0 0.005 0.01 0.02 --trials 20
"""
import os
import sys
import argparse
import hashlib
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore", category=FutureWarning)

import numpy as np

from framework import config
from framework.data import Data, load_revenue, load_t86
from framework import search as S

ap = argparse.ArgumentParser()
ap.add_argument("--effects", type=float, nargs="+",
                default=[0.0, 0.0025, 0.005, 0.01, 0.02, 0.04],
                help="注入的真 edge(每 20 日,小數)")
ap.add_argument("--trials", type=int, default=20, help="第六關置換次數")
ap.add_argument("--pick", type=float, default=0.10, help="每日選股比例")
ap.add_argument("--hold", type=int, default=20)
ap.add_argument("--is-end", default=config.OOS2_FROM)
ap.add_argument("--max-active", type=int, default=3)
args = ap.parse_args()

print("=" * 78)
print("驗證器自我驗證:陽性對照(注入已知 edge,看關卡抓不抓得到)")
print("=" * 78)

data = Data()
panel = S.build_panel(data, revenue=load_revenue(), inst=load_t86(), is_end=args.is_end)
dates, sid = panel["dates"], panel["sid"]
H = args.hold
print(f"面板 {len(dates)} 列  持有 {H} 日  每日選前 {args.pick*100:.0f}%  "
      f"樣本內 <{args.is_end}")

# ---- 無技術含量的選股規則:雜湊(sid+date)→ 偽隨機分數,每日取前 pick% ----
score = np.array([int(hashlib.md5(f"{s}|{d}".encode()).hexdigest()[:8], 16) % 10**6
                  for s, d in zip(sid, dates)], dtype=float)
sel = np.zeros(len(dates), dtype=bool)
for dt in np.unique(dates):
    m = np.flatnonzero(dates == dt)
    if len(m) < 20:
        continue
    k = max(1, int(len(m) * args.pick))
    sel[m[np.argsort(-score[m])[:k]]] = True
print(f"規則命中 {int(sel.sum())} 列 = {len(np.unique(sid[sel]))} 檔 × "
      f"{len(np.unique(dates[sel]))} 個日期(這條規則本身零預測力)")

fwd0 = panel["fwd"][H].copy()
holds = panel["holds"]

print(f"\n{'注入 edge':>10}{'量到(vs peer)':>16}{'bootstrap p':>13}"
      f"{'第六關':>9}{'第七關':>9}   總判決")
print("-" * 78)

results = []
for E in args.effects:
    # 注入:被選中列的前向報酬 + E×(H/20)(把每20日的 E 換算成該持有期)
    panel["fwd"][H] = fwd0 + sel * (E * H / 20.0)
    for h in holds:                      # 其他持有期不動,搜尋才不會被干擾
        pass

    # 這條規則加進搜尋空間當一個維度(才測得到第六關)
    preds = S.build_predicates(panel, wide=False)
    preds["synth"] = [("不管", None), ("合成訊號", sel)]
    combos = S.iter_combos(preds, args.max_active)

    panel_h = dict(panel)
    panel_h["holds"] = [H]               # 只掃該持有期,省時
    res = S.scan(panel_h, preds, combos=combos)
    if not res:
        print(f"{E*100:>9.2f}%   無有效組合")
        continue
    best = res[0]

    # 量到的效果(vs peer,只看這條規則本身)
    ex_peer = S.peer_excess(panel_h, H)
    r = S.evaluate(panel_h, sel, H)
    measured = float(np.nanmean(ex_peer[sel]) * 20.0 / H)

    from framework import gates
    v = ex_peer[sel]
    v = v[np.isfinite(v)]
    b = gates.bootstrap(v.tolist())
    pval = b[3] if b else float("nan")

    # 第六關要問的是「**這條合成訊號**贏不贏得過亂掃的最好成績」,
    # 不是「全場最佳組」(那會被真實資料的雜訊冠軍蓋台,量錯東西)。
    synth_score = r[0] if r else -9
    p6, _ = S.best_of_n_test(panel_h, preds, synth_score, trials=args.trials,
                             combos=combos, verbose=False)
    g6 = p6 <= 0.05
    c = S.concentration(panel_h, sel, H, ex=ex_peer)
    # 靜默評估第七關
    g7 = bool(c and c["rel_stock_median"] > 0 and c["top5_share"] < 40
              and c["drop_top3_norm20"] >= c["norm20"] * 0.5)

    verdict = "🟢 抓到" if (g6 and g7) else ("🟡 只過第六關" if g6 else "🔴 沒抓到")
    print(f"{E*100:>9.2f}%{measured*100:>15.2f}%{pval:>13.4f}"
          f"{'✅' if g6 else '❌':>8}{'✅' if g7 else '❌':>8}   {verdict}"
          + ("   ← 最佳組不是合成訊號" if best["labels"].get("synth") == "不管" else ""))
    results.append((E, measured, g6, g7, c))

panel["fwd"][H] = fwd0   # 還原

print("\n— 偵測門檻 —")
passed = [E for E, _, g6, g7, _ in results if g6 and g7 and E > 0]
if passed:
    print(f"  關卡能抓到的最小 edge = 每20日 **{min(passed)*100:.2f}%**"
          f"(年化約 {((1+min(passed))**12-1)*100:.0f}%)")
else:
    print("  ⚠️ 連最大的注入 edge 都沒抓到 → 關卡過嚴,前面所有判死結論需重新檢視。")
fp = [E for E, _, g6, g7, _ in results if E == 0 and g6 and g7]
print(f"  E=0(無 edge)是否被誤判為有:{'⚠️ 是(假陽性)' if fp else '否 ✅'}")

print("\n— 最大注入強度下的第七關細節(看關卡在挑剔什麼)—")
if results:
    E, measured, g6, g7, c = results[-1]
    print(f"  注入 {E*100:.2f}%/20日:")
    S.report_concentration(c, indent="    ")

print("\n" + "=" * 78)
print("判讀:若偵測門檻遠低於真實訊號的效果量,代表關卡沒有系統性殺好訊號;")
print("      真實訊號被判死 = 市場真的沒有那麼大的 edge,不是驗證器的偏見。")
print("=" * 78)
