#!/usr/bin/env python3
"""
驗證:0050 定期定額 + 布林通道/KD 擇時加減碼,真的比「傻傻定額」成本更低、報酬更好嗎?

策略(受測者說法):
  跌破布林下軌 且 KD<20(甚至黃金交叉)→ 扣款 1萬 拉到 3萬/5萬(加碼低點)
  漲到布林上軌 且 KD>80(超買)     → 扣款降到 1000 甚至暫停
對照:固定定額(每期都 1×)。

公平比法:同一組交易日、同一標的(0050)。價格走 framework 的後復權
(Data 已抹平 2025-06-18 的 1:4 分割,否則布林/成本全錯)。
比 平均成本 / 每元報酬(ROI=市值/投入-1)/ 年化資金加權報酬。
關鍵:加碼會讓「總投入」變多 → 不能只看市值大小,要看「每一塊錢的效率」。
用法: python studies/dca_bbkd.py
"""
import os
import sys
import statistics
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from framework.data import Data

BENCH = "0050"
DATA = Data(bench=BENCH)          # 載入即後復權(split_adjusted 抹平公司行為斷點)
adj = DATA.d[BENCH]

dates = [b["date"] for b in adj]
close = [b["close"] for b in adj]
high = [b["max"] for b in adj]
low = [b["min"] for b in adj]
N = len(adj)

# ---- 布林通道(20, 2σ)----
MB, UB, LB = [None] * N, [None] * N, [None] * N
for i in range(19, N):
    w = close[i - 19:i + 1]
    m = statistics.mean(w)
    sd = statistics.pstdev(w)
    MB[i], UB[i], LB[i] = m, m + 2 * sd, m - 2 * sd

# ---- KD(9,3,3)----
K, D = [50.0] * N, [50.0] * N
for i in range(1, N):
    lo = min(low[max(0, i - 8):i + 1])
    hi = max(high[max(0, i - 8):i + 1])
    rsv = (close[i] - lo) / (hi - lo) * 100 if hi > lo else 50.0
    K[i] = K[i - 1] * 2 / 3 + rsv / 3
    D[i] = D[i - 1] * 2 / 3 + K[i] / 3


def golden(i):
    return i > 0 and K[i - 1] <= D[i - 1] and K[i] > D[i]


# ---- 每日扣款倍率 ----
def mult_signal(i, pause_top=False):
    if UB[i] is None:
        return 1.0
    if close[i] < LB[i] and K[i] < 20:
        return 5.0 if golden(i) else 3.0          # 低點加碼
    if close[i] > UB[i] and K[i] > 80:
        return 0.0 if pause_top else 0.1          # 超買減碼/暫停
    return 1.0


# ---- 模擬:每交易日投入 base×倍率,買進 base/price 股 ----
def simulate(mult_fn):
    base = 10000.0
    invested = shares = 0.0
    cashflows = []   # (date, -投入)  最後加市值
    start = 19       # 等布林暖身
    for i in range(start, N):
        amt = base * mult_fn(i)
        if amt > 0:
            invested += amt
            shares += amt / close[i]
            cashflows.append((dates[i], -amt))
    final = shares * close[-1]
    return invested, shares, final, cashflows


def irr_annual(cashflows, final, last_date, first_date):
    """資金加權年化報酬(月現金流 → 年化 IRR,二分法)。"""
    flows = list(cashflows) + [(last_date, final)]
    d0 = date.fromisoformat(first_date)

    def npv(r):
        s = 0.0
        for dt, cf in flows:
            t = (date.fromisoformat(dt) - d0).days / 365.0
            s += cf / (1 + r) ** t
        return s
    lo, hi = -0.99, 5.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def report(name, res):
    invested, shares, final, cf = res
    avg_cost = invested / shares
    roi = final / invested - 1
    irr = irr_annual(cf, final, dates[-1], dates[19])
    print(f"  {name}")
    print(f"    總投入 {invested:,.0f}  持股 {shares:,.1f}  期末市值 {final:,.0f}")
    print(f"    平均成本/股 {avg_cost:.2f}   每元報酬 ROI {roi*100:+.1f}%   年化(資金加權) {irr*100:+.1f}%")
    return avg_cost, roi, irr


print("=" * 70)
print(f"0050 定期定額擇時 vs 固定  期間 {dates[0]}~{dates[-1]}  {N}交易日"
      f"(後復權:全市場抹平 {DATA.split_adjusted} 個公司行為斷點)")
print(f"末價(還原後) {close[-1]:.2f}")
print("=" * 70)
a = report("[A] 固定定額(對照)", simulate(lambda i: 1.0))
print()
b = report("[B] 布林+KD 加減碼(超買降到0.1×)", simulate(lambda i: mult_signal(i)))
print()
c = report("[C] 布林+KD 加減碼(超買暫停 0×)", simulate(lambda i: mult_signal(i, pause_top=True)))

print("\n— 判決 —")
for nm, x in (("B", b), ("C", c)):
    dc = (x[0] / a[0] - 1) * 100      # 平均成本差
    dr = (x[1] - a[1]) * 100          # ROI 差(百分點)
    di = (x[2] - a[2]) * 100          # 年化差
    print(f"  {nm} vs A:平均成本 {dc:+.1f}%  ROI {dr:+.1f}pp  年化 {di:+.1f}pp"
          + ("  → 擇時有幫助" if x[1] > a[1] else "  → 沒贏對照"))
