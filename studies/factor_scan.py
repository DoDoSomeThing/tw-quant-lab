#!/usr/bin/env python3
"""
離線因子掃描(純 kline)。測 4 個「沒乾淨測過」的角度,全走五關。基準=全市場等權。
  ① 12-1 月動能(橫斷面,月再平衡)
  ② 爆量超額延續(事件:量暴衝→隔開盤進→持有 N 日)
  ③ 低波動異常(月再平衡)
  ④ regime × 動能(只在 0050 站上 MA120 時做 ①)
用法: python studies/factor_scan.py
"""
import os
import sys
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from framework import config
from framework.data import Data
from framework import gates

COST = config.COST
COST_LO, COST_HI = config.COST_LO, config.COST_HI
DECILE = 0.10
MIN_PICK = 5

D = Data()
cal, C, cidx, regime, BENCH = D.cal, D.C, D.cidx, D.regime, D.bench
EW = {h: D.build_ew(h) for h in (5, 10, 21)}

# 月再平衡日:主日曆每 21 根取一個(暖身 252 起)
REB = [i for i in range(252, len(cal) - 21, 21)]


# ---------- 因子 1/3/4:月再平衡橫斷面 ----------
def cross_factor(signal_fn, pick_top, regime_gate=False, cost=COST):
    monthly = []
    for i in REB:
        dt = cal[i]
        if regime_gate and not regime[dt]:
            monthly.append((dt, 0.0))
            continue
        fwd_dt = cal[i + 21]
        if dt not in EW[21]:
            continue
        bret = EW[21][dt]
        cand = []
        for t in D.d:
            if t == BENCH:
                continue
            sig = signal_fn(t, dt, i)
            if sig is None:
                continue
            if dt not in C[t] or fwd_dt not in C[t] or C[t][dt] <= 0:
                continue
            cand.append((sig, C[t][fwd_dt] / C[t][dt] - 1))
        if len(cand) < MIN_PICK * 2:
            continue
        cand.sort(reverse=pick_top)
        k = max(MIN_PICK, int(len(cand) * DECILE))
        port = statistics.mean(r for _, r in cand[:k])
        monthly.append((dt, (port - cost) - bret))
    return monthly


def sig_mom(t, dt, i):
    d12, d1 = cal[i - 252], cal[i - 21]
    if d12 in C[t] and d1 in C[t] and C[t][d12] > 0:
        return C[t][d1] / C[t][d12] - 1
    return None


def sig_lowvol(t, dt, i):
    window = cal[i - 60:i]
    cl = [C[t][x] for x in window if x in C[t] and C[t][x] > 0]
    if len(cl) < 50:
        return None
    rets = [cl[j] / cl[j - 1] - 1 for j in range(1, len(cl))]
    return statistics.pstdev(rets)


# ---------- 因子 2:爆量超額延續(事件) ----------
def volspike(hold, mult=3.0, cost=COST):
    trades = []
    for t in D.d:
        if t == BENCH:
            continue
        bars = D.d[t]
        for j in range(20, len(bars) - hold - 1):
            vol = bars[j]["volume"]
            ma = sum(bars[k]["volume"] for k in range(j - 20, j)) / 20
            if ma <= 0 or vol < mult * ma:
                continue
            if bars[j]["close"] <= bars[j - 1]["close"]:
                continue
            if j + 1 + hold >= len(bars):
                continue
            ent_dt = bars[j + 1]["date"]
            if bars[j + 1]["close"] <= 0:
                continue
            sret = bars[j + 1 + hold]["close"] / bars[j + 1]["close"] - 1
            if ent_dt not in EW[hold]:
                continue
            trades.append((ent_dt, (sret - cost) - EW[hold][ent_dt]))
    return trades


print("=" * 70)
print(f"資料 {cal[0]}~{cal[-1]}  {len(cal)}交易日  {len(D.d)}檔  基準=全市場等權  成本來回{COST*100:.1f}%")
print("=" * 70)

print("\n① 12-1 月動能(取前10%,月再平衡)")
gates.report("無regime", cross_factor(sig_mom, True), monthly=True)
print("\n④ regime×動能(只在0050>MA120時做)")
gates.report("有regime", cross_factor(sig_mom, True, regime_gate=True), monthly=True)

print("\n③ 低波動異常(取後10%最低波動,月再平衡)")
gates.report("無regime", cross_factor(sig_lowvol, False), monthly=True)
gates.report("有regime", cross_factor(sig_lowvol, False, regime_gate=True), monthly=True)

print("\n② 爆量超額延續(量>3xMA20+紅K→隔日進)")
for h in (5, 10):
    gates.report(f"持有{h}日", volspike(h))

print("\n— 成本敏感(① 12-1動能,換成本)—")
for c in (COST_LO, COST_HI):
    gates.report(f"成本{c*100:.1f}%", cross_factor(sig_mom, True, cost=c), monthly=True)

print("\n— regime 污染檢查 —")
gates.regime_health(D)
print("\n做完。alpha 看『月超額均』是否穩定為正、OOS(2024)沒崩、成本拉高沒翻負。")
