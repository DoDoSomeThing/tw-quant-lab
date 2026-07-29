#!/usr/bin/env python3
"""
專攻營收 YoY —— 唯一在回推搜尋中反覆出現的成分,單獨拉出來用「不切細」的方式測。

為什麼要重測:回推搜尋的 Top 組合全部死在第七關(少數股票撐起來),
但每一組都含「營收YoY>20%」。所以問題變成:**營收 YoY 本身是不是一個因子?**
還是它只是幫忙選中了那幾檔飆股?

設計(刻意反過來做,避開前面的坑):
  - 單一條件,不疊濾網 → 樣本大,不會切到 n=100
  - **橫斷面分位**(每個再平衡日內把 YoY 排序分 10 組)→ 避開 YoY 分布隨年份漂移
    (2021 有低基期效應,絕對門檻在不同年份意義不同)
  - 同時看 **平均 + 中位數 + 每檔中位數**(平均會被少數飆股綁架)
  - 基準同時用 全市場等權 與 **同規模 peer**(擋規模因子假象)
  - 看**單調性**:分位越高報酬越好才像因子;只有最高一格好 = 可疑
  - 多空組合(最高分位 − 最低分位)= 標準因子檢定
  - 最高分位跑第七關集中度

只在樣本內(<2025-01-01)。OOS 依然鎖著。
用法: python studies/revenue_yoy_deep.py
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
from framework import gates

ap = argparse.ArgumentParser()
ap.add_argument("--is-end", default=config.OOS2_FROM)
ap.add_argument("--bins", type=int, default=10)
ap.add_argument("--no-inst", action="store_true")
args = ap.parse_args()

print("=" * 78)
print("營收 YoY 深度檢定(單一因子,不疊濾網)")
print("=" * 78)

data = Data()
panel = S.build_panel(data, revenue=load_revenue(),
                      inst=None if args.no_inst else load_t86(),
                      is_end=args.is_end)
f, dates = panel["feat"], panel["dates"]
yoy = f["yoy"]
print(f"面板 {len(dates)} 列  有營收 YoY 的 {int(np.isfinite(yoy).sum())} 列 "
      f"({np.isfinite(yoy).mean()*100:.0f}%)  樣本內 <{args.is_end}")

# ---- 橫斷面分位:每個再平衡日內,把當日所有有 YoY 的股票排序分 N 組 ----
B = args.bins
qbin = np.full(len(yoy), -1, dtype=int)
for dt in np.unique(dates):
    m = (dates == dt) & np.isfinite(yoy)
    if m.sum() < B * 5:
        continue
    idx = np.flatnonzero(m)
    order = np.argsort(yoy[idx])
    ranks = np.empty(len(idx), dtype=float)
    ranks[order] = np.arange(len(idx))
    qbin[idx] = np.minimum((ranks / len(idx) * B).astype(int), B - 1)


def stats(mask, ex):
    v = ex[mask & np.isfinite(ex)]
    if len(v) < 30:
        return None
    return dict(n=len(v), mean=v.mean(), median=np.median(v), pos=(v > 0).mean() * 100)


def per_stock_median(mask, ex):
    m = mask & np.isfinite(ex)
    ss, vv = panel["sid"][m], ex[m]
    if len(vv) < 30:
        return None
    uniq = np.unique(ss)
    per = np.array([np.median(vv[ss == s]) for s in uniq])
    return np.median(per), (per > 0).mean() * 100, len(uniq)


for h in panel["holds"]:
    ew_ex = (panel["fwd"][h] - config.COST) - panel["ew"][h]
    pe_ex = S.peer_excess(panel, h)
    k = 20.0 / h    # 標準化到每 20 日

    print("\n" + "=" * 78)
    print(f"持有 {h} 日 — YoY 橫斷面 {B} 分位(0=最低 … {B-1}=最高),標準化每20日")
    print("=" * 78)
    print(f"  {'分位':<6}{'n':>7}{'vs等權均':>10}{'vs peer均':>11}{'peer中位':>10}"
          f"{'勝率':>7}{'每檔中位':>10}{'檔數正%':>9}")
    curve = []
    for b in range(B):
        m = qbin == b
        a = stats(m, ew_ex)
        p = stats(m, pe_ex)
        ps = per_stock_median(m, pe_ex)
        if not (a and p):
            continue
        curve.append(p["mean"] * k)
        print(f"  {b:<6}{a['n']:>7}{a['mean']*k*100:>9.2f}%{p['mean']*k*100:>10.2f}%"
              f"{p['median']*k*100:>9.2f}%{p['pos']:>6.0f}%"
              + (f"{ps[0]*k*100:>9.2f}%{ps[1]:>8.0f}%" if ps else f"{'—':>9}{'—':>9}"))

    # 單調性:分位序號與超額的相關(Spearman 近似 = 用秩相關)
    if len(curve) >= 5:
        c = np.array(curve)
        r = np.corrcoef(np.arange(len(c)), c)[0, 1]
        print(f"\n  單調性(分位序號 vs vs-peer超額 相關係數)= {r:+.2f}"
              + ("  → 越高越好,像因子" if r > 0.6 else
                 "  → 不單調,只有個別分位好 = 可疑"))

    # 多空:最高分位 − 最低分位(標準因子組合)
    hi = stats(qbin == B - 1, pe_ex)
    lo = stats(qbin == 0, pe_ex)
    if hi and lo:
        print(f"  多空(最高−最低,vs peer)= {(hi['mean']-lo['mean'])*k*100:+.2f}%/20日"
              f"   中位差 {(hi['median']-lo['median'])*k*100:+.2f}%")

# ---- 最高分位:第七關集中度 + 分年 ----
H = 20
ex20 = S.peer_excess(panel, H)
top = qbin == B - 1
print("\n" + "=" * 78)
print(f"最高分位(YoY 前 {100//B}%)× 持有 {H} 日 — 第七關集中度")
print("=" * 78)
S.report_concentration(S.concentration(panel, top, H, ex=ex20))

print("\n— 分年(vs peer,標準化每20日)—")
pairs = [(d, v) for d, v in zip(dates[top & np.isfinite(ex20)],
                                ex20[top & np.isfinite(ex20)])]
gates.report("YoY最高分位", pairs, oos_from="9999")
gates.report_boot("bootstrap", [v for _, v in pairs])

# ---- 絕對門檻版(對照:回推搜尋用的是 YoY>20%)----
print("\n" + "=" * 78)
print(f"對照:絕對門檻版(持有 {H} 日,vs peer,標準化每20日)")
print("=" * 78)
for lo_, hi_, lab in [(-9, 0, "YoY<0"), (0, 0.1, "0~10%"), (0.1, 0.2, "10~20%"),
                      (0.2, 0.3, "20~30%"), (0.3, 0.5, "30~50%"), (0.5, 9, ">50%")]:
    m = np.isfinite(yoy) & (yoy >= lo_) & (yoy < hi_)
    p = stats(m, ex20)
    ps = per_stock_median(m, ex20)
    if p:
        print(f"  {lab:<8} n={p['n']:>6}  均{p['mean']*100:+6.2f}%  "
              f"中位{p['median']*100:+6.2f}%  勝率{p['pos']:.0f}%"
              + (f"  每檔中位{ps[0]*100:+6.2f}%(正{ps[1]:.0f}%)" if ps else ""))

print("\n" + "=" * 78)
print("判讀:要像真因子 → ①分位單調 ②中位數(不只平均)為正 ③每檔中位為正")
print("      ④過第七關(不是少數股票撐)⑤各年不崩。任一不過 = 別當 edge。")
print("=" * 78)
