#!/usr/bin/env python3
"""
營收動能「連續下修」驗證 —— 代理「分析師 EPS 預估連續下修」。

原假設要分析師共識 EPS 的 revision，台股免費資料拿不到（TEJ/IBES 付費、
自願性財測樣本近零）。改用因果上游：月營收 YoY 的連續遞減。營收每月 10 日前
先公布，EPS 預估之後才改 —— 時效更好，且完全 point-in-time。

已結案（不重驗）：YoY「水準」最低分位 = -1.19%/20日、比隨機爛。
本腳本測的是**沒驗過的「持續性」**：還在變爛 vs 已經爛，是兩件事。

事前登記假設 H1：連續下修組的 peer 超額顯著為負（百分位 <= 5）。
判定用 framework/calibrate.py 的事前規則，α=0.05，人不填門檻。

用法:
  export QLAB_KLINE_FILE=kline_deep_long.json
  python studies/revenue_downgrade.py                 # 樣本內
  python studies/revenue_downgrade.py --rebalance 5   # 週再平衡(樣本4倍,重疊也4倍)
"""
import os
import sys
import argparse
import statistics
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
ap.add_argument("--is-end", default=config.OOS2_FROM, help="樣本內結束日(OOS 鎖著)")
# 以下兩個只影響「看哪一段」,不動任何指標/持有期/分位數定義。預設值 = 原樣本內行為。
ap.add_argument("--is-start", default=None,
                help="只保留 date >= 此值的列。開 OOS 時設 2025-01-01(需搭 --is-end 9999)")
ap.add_argument("--oos-from", default="9999",
                help="gates.report 的 IS/OOS 切點。預設 9999 = 關閉")
ap.add_argument("--rebalance", type=int, default=21)
ap.add_argument("--min-streak", type=int, default=3)
ap.add_argument("--n-random", type=int, default=200)
args = ap.parse_args()

print("=" * 78)
print("營收連續下修 —— 「還在變爛」是不是負面訊號")
print("=" * 78)

data = Data()
revenue = load_revenue()

# ---------- 每檔的 YoY 序列 + 連續遞減/遞增 streak（point-in-time）----------
def yoy_streaks(rows):
    """
    rows = [[avail_date, yyyymm, rev], ...]
    回 (avails, yoys, dn_streak, up_streak)，皆按 avail_date 排序。
    dn_streak = 到該月為止，連續幾個月 YoY 低於前一個月（要求月份連續，跳月則歸零）。
    """
    by_ym = {r[1]: r[2] for r in rows if r[2]}
    seq = []
    for avail, ym, rev in sorted(rows, key=lambda x: x[1]):
        if not rev:
            continue
        prev = by_ym.get(ym - 100)          # 去年同月
        if not prev or prev <= 0:
            continue
        seq.append((avail, ym, rev / prev - 1))
    avails, yms, yoys = [], [], []
    dn, up = [], []
    d = u = 0
    for i, (avail, ym, y) in enumerate(seq):
        if i > 0:
            prev_ym, prev_y = yms[-1], yoys[-1]
            # 月份必須相鄰（202403 的前一個月是 202402；跨年 202401 → 202312）
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

all_avail = np.concatenate([v[0] for v in REV.values()])
# avail_date 是字串 dtype('<U10')，numpy 2.x 的 min/max 沒有對應 ufunc loop → 用內建
avail_lo, avail_hi = min(all_avail.tolist()), max(all_avail.tolist())
print(f"\n營收資料:{len(REV)} 檔  avail_date {avail_lo} ~ {avail_hi}")
print(f"K線資料:{len(data.d)} 檔  {data.cal[0]} ~ {data.cal[-1]}"
      f"  (後復權抹平 {data.split_adjusted} 個斷點)")
if avail_lo > data.cal[0]:
    print(f"⚠️  營收比 K 線短 —— 有效期間受營收限制，起點 {avail_lo}")

# ---------- 面板 ----------
panel = S.build_panel(data, revenue=revenue, inst=None,
                      rebalance=args.rebalance, is_end=args.is_end)
dates, sids = panel["dates"], panel["sid"]
yoy = panel["feat"]["yoy"]

# 期間濾網:只影響取樣區段,不改任何訊號定義。None → 全收(原行為)。
PER = (dates >= args.is_start) if args.is_start else np.ones(len(dates), dtype=bool)
if args.is_start:
    print(f"\n⚙️  期間濾網:只取 date >= {args.is_start} → "
          f"{int(PER.sum())}/{len(dates)} 列、{len(np.unique(dates[PER]))} 個再平衡日")

n_dates = len(np.unique(dates[PER]))
print(f"\n面板 {int(PER.sum())} 列 × {n_dates} 個再平衡日 (rebalance={args.rebalance})")
for h in panel["holds"]:
    print(f"  持有 {h:>2} 日 → 重疊 {h/args.rebalance:.1f} 倍，"
          f"有效獨立日期 ≈ {n_dates/(h/args.rebalance):.0f}")

# 把 streak 對齊到面板每一列（每列取 avail_date <= 該再平衡日 的最新一筆）
dn_arr = np.full(len(dates), np.nan)
up_arr = np.full(len(dates), np.nan)
for sid in np.unique(sids):
    s = REV.get(sid)
    if s is None:
        continue
    m = np.flatnonzero(sids == sid)
    pos = np.searchsorted(s[0], dates[m], side="right") - 1
    ok = pos >= 0
    dn_arr[m[ok]] = s[2][pos[ok]]
    up_arr[m[ok]] = s[3][pos[ok]]

print(f"有 streak 的列:{int(np.isfinite(dn_arr).sum())} "
      f"({np.isfinite(dn_arr).mean()*100:.0f}%)")

# 橫斷面 YoY 分位（對照組 C，用來確認腳本沒寫壞：最低分位應該重現 ≈ -1.2%）
B = 10
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


def line(lab, mask, ex, k):
    mask = mask & PER
    v = ex[mask & np.isfinite(ex)]
    if len(v) < 30:
        print(f"  {lab:<22}{'樣本不足':>10}  n={len(v)}")
        return
    ss = sids[mask & np.isfinite(ex)]
    uniq = np.unique(ss)
    per = np.array([v[ss == s].mean() for s in uniq])
    print(f"  {lab:<22}{v.mean()*k*100:>8.2f}%{np.median(v)*k*100:>9.2f}%"
          f"{(v > 0).mean()*100:>7.0f}%{np.median(per)*k*100:>9.2f}%"
          f"{len(v):>8}{len(uniq):>7}")


# ---------- 主結果 ----------
K = args.min_streak
for h in panel["holds"]:
    if h not in (20, 60):
        continue
    ex = S.peer_excess(panel, h)
    k = 20.0 / h
    print("\n" + "=" * 78)
    print(f"持有 {h} 日 — vs 同日×同規模 peer，扣 {config.COST*100:.1f}% 成本，"
          f"標準化每 20 日")
    print("=" * 78)
    print(f"  {'組別':<22}{'平均':>9}{'中位':>9}{'勝率':>7}{'每檔中位':>9}"
          f"{'列數':>8}{'檔數':>7}")

    print("  — 單調性:連續下修月數 —")
    curve = []
    for s in range(5):
        m = (dn_arr == s) if s < 4 else (dn_arr >= 4)
        lab = f"下修 {s} 個月" if s < 4 else "下修 4+ 個月"
        v = ex[m & PER & np.isfinite(ex)]
        if len(v) >= 30:
            curve.append(v.mean() * k)
        line(lab, m, ex, k)
    if len(curve) >= 4:
        c = np.array(curve)
        r = np.corrcoef(np.arange(len(c)), c)[0, 1]
        print(f"  → 單調性(下修月數 vs 超額 相關)= {r:+.2f}"
              + ("  越下修越差,像真訊號" if r < -0.6 else
                 "  越下修越好(與假設相反)" if r > 0.6 else "  不單調"))

    print("  — 訊號組 —")
    sig_A = dn_arr >= K
    sig_B = (dn_arr >= K) & np.isfinite(yoy) & (yoy < 0)
    sig_Ap = up_arr >= K
    line(f"A 下修≥{K}月", sig_A, ex, k)
    line(f"B 下修≥{K}月 且 YoY<0", sig_B, ex, k)
    line(f"A′ 連續上修≥{K}月", sig_Ap, ex, k)
    print("  — 對照組(已知答案,用來驗腳本)—")
    line("C YoY 最低分位", qbin == 0, ex, k)
    line("C′ YoY 最高分位", qbin == B - 1, ex, k)

# ---------- 校準判定(事前登記規則)----------
H = 60
ex60 = S.peer_excess(panel, H)
print("\n" + "=" * 78)
print(f"校準判定 — 持有 {H} 日，對照 {args.n_random} 組同規模隨機選股")
print("=" * 78)
for lab, m in [(f"A 下修≥{K}月", dn_arr >= K),
               (f"B 下修≥{K}月且YoY<0", (dn_arr >= K) & np.isfinite(yoy) & (yoy < 0)),
               (f"A′ 上修≥{K}月", up_arr >= K),
               ("C YoY最低分位(對照)", qbin == 0)]:
    print(f"\n【{lab}】")
    CAL.report(CAL.calibrate(panel, m & PER, H, ex=ex60, n_random=args.n_random))

# ---------- 分年 + bootstrap ----------
print("\n" + "=" * 78)
print(f"A 下修≥{K}月 × 持有 {H} 日 — 分年")
print("=" * 78)
mA = (dn_arr >= K) & PER & np.isfinite(ex60)
gates.report(f"下修≥{K}月", list(zip(dates[mA], ex60[mA])), oos_from=args.oos_from)
gates.report_boot("bootstrap", list(ex60[mA]))
S.report_concentration(S.concentration(panel, (dn_arr >= K) & PER, H, ex=ex60))

print("\n" + "=" * 78)
print("判讀(事前登記,不准跑完改):")
print("  超額百分位 <= 5  → H1 成立,連續下修 = 負面訊號,便宜不是理由")
print("  超額百分位 >= 95 → 與現有判決衝突,要重查")
print("  5 ~ 95          → 無法判定(對照偵測門檻 0.10~0.25%/20日),不等於沒有")
print("  對照組 C 若沒重現 ≈ -1.2%/20日 → 腳本有問題,上面全部作廢")
print("=" * 78)
