#!/usr/bin/env python3
"""
關6+關7 加嚴複驗 —— 給「過了五關的訊號」上的第二層刑求。

  關6 參數穩健性:訊號/引擎參數開網格,每格重跑超額。
      只有一格神、隔壁就爆 = 擬合噪音,不是 edge。
  關7 子期間穩健性:滾動 12 期(≈12個月)窗的超額勝率。
      單一年撐起全場 = 風格運氣,不是 edge。

主角:大型×高YoY×非空頭×MA60×動能(2026-07-12 過五關的唯一案例)
陪跑:12-1 月動能(五關 🟡,對照組)

用還原價跑:export QLAB_PRICE=adj
用法:python studies/gate67_combo.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from framework import config
from framework.data import Data, load_revenue
from framework.context import Context
from framework.engine import run
from framework import gates

t0 = time.time()
data = Data()
ctx = Context(data, revenue=load_revenue())
print("=" * 72)
print(f"關6+關7 加嚴複驗  資料 {data.cal[0]}~{data.cal[-1]}  {len(data.d)}檔  "
      f"價格模式 {config.PRICE_MODE}")
print("=" * 72)


def combo_factory(yoy_th=0.2, ma_n=60, mom_n=60, size_p=0.5):
    """revenue combo 的參數化版本(基準組 = 五關過關那組)。"""
    size_cut = ctx.size_pct(size_p)

    def signal(sid, date, c):
        if c.avgvol(sid) < size_cut:
            return None
        if c.regime(date) is not True:
            return None
        yoy = c.revenue_yoy(sid, date)
        if yoy is None or yoy < yoy_th:
            return None
        px = c.close(sid, date)
        ma = c.ma(sid, date, ma_n)
        mom = c.momentum(sid, date, lookback=mom_n)
        if px is None or ma is None or mom is None:
            return None
        if px <= ma or mom <= 0:
            return None
        return yoy
    return signal


def mom_signal(sid, date, c):
    return c.momentum(sid, date, lookback=252, gap=21)


def cell(signal, hold=20, top_pct=1.0):
    pairs, _ = run(signal, data, ctx, hold=hold, rebalance=hold + 1, top_pct=top_pct)
    return pairs


# ================= 主角:revenue combo =================
print("\n【主角】大型×高YoY×非空頭×MA60×動能(基準組:yoy>20% ma60 mom60 hold20)")

print("\n關6A 訊號參數網格:YoY門檻 × 動能/均線窗")
cells = []
for yoy in (0.10, 0.20, 0.30):
    for w in (20, 60, 120):
        cells.append((f"yoy>{yoy:.0%} 窗{w}日", cell(combo_factory(yoy_th=yoy, ma_n=w, mom_n=w))))
f6a = gates.sweep_report(cells)

print("\n關6B 引擎參數網格:持有期 × 規模門檻")
cells = []
for hold in (10, 20, 40):
    for sp in (0.3, 0.5, 0.7):
        cells.append((f"持有{hold}日 量前{100-sp*100:.0f}%",
                      cell(combo_factory(size_p=sp), hold=hold)))
f6b = gates.sweep_report(cells)

print("\n關7 子期間穩健(基準組,滾動12期窗)")
base = cell(combo_factory())
f7 = gates.subperiod(base)

print("\n【主角判決】")
ok6 = f6a[0] >= 0.8 and f6b[0] >= 0.8 and f6a[1] > -0.01 and f6b[1] > -0.01
ok7 = bool(f7) and f7[0] >= 0.65 and f7[1] > -0.02
print(f"  關6 {'🟢 過' if ok6 else '🔴 不過'}  關7 {'🟢 過' if ok7 else '🔴 不過'}"
      f"  → {'✅ 七關全過:候選 edge 升級,可進紙上模擬' if ok6 and ok7 else '❌ 加嚴不過:五關表現=參數/時段運氣,不升級'}")

# ================= 陪跑:12-1 動能 =================
print("\n【陪跑】12-1 月動能")
print("\n關6 引擎參數網格:持有期 × 取前比例")
cells = []
for hold in (10, 20, 40):
    for tp in (0.05, 0.10, 0.20):
        cells.append((f"持有{hold}日 前{tp:.0%}", cell(mom_signal, hold=hold, top_pct=tp)))
m6 = gates.sweep_report(cells)

print("\n關7 子期間穩健(hold20 前10%)")
m7 = gates.subperiod(cell(mom_signal, top_pct=0.10))
ok6m = m6[0] >= 0.8 and m6[1] > -0.01
ok7m = bool(m7) and m7[0] >= 0.65 and m7[1] > -0.02
print(f"\n【陪跑判決】關6 {'🟢' if ok6m else '🔴'}  關7 {'🟢' if ok7m else '🔴'}")

print(f"\n耗時 {time.time()-t0:.0f}s")
