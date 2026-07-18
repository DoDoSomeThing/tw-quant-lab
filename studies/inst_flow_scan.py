#!/usr/bin/env python3
"""
法人動向訊號驗證 —— 定位:不是找策略,是拆穿。
假設「法人買賣超是公開資料、被全市場抄爛、edge 不存在」,讓五關盡力打臉。

訊號(全部事前寫死,見 100_Todo/2026-07-18_法人動向訊號驗證_SPEC.md):
  H1 外資連買 ≥N 日(N=3、5)
  H2 投信連買 ≥N 日(N=3、5)
  H3 外資轉向買:前 ≥3 日連賣、最新一日翻淨買
  H4 法人合力:外資與投信同日皆淨買
  H5 買超強度:外資淨買股數 / 20 日均量 ≥ 10%
持有期 H ∈ {5, 20}。7 訊號 × 2 持有期 = 14 格,一次跑完全報,不挑格。

Point-in-time:訊號日 dt 只用 dt-1(含)以前的 t86(收盤後公布,當日不可見),
沿用 ctx.inst_buy 慣例;進場 = dt 收盤(等同「公布次日進場」),close-to-close。
連買往回數,途中缺資料(未上市/停牌/當日無法人)即中斷,不補零。

用還原價跑:export QLAB_PRICE=adj
用法:python studies/inst_flow_scan.py
"""
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from framework import config
from framework.data import Data, load_t86
from framework.context import Context
from framework.engine import run
from framework import gates

t0 = time.time()
data = Data()
inst = load_t86()
ctx = Context(data, inst=inst)
cal, cidx = data.cal, data.cidx

t86_dates = sorted({dt for m in inst.values() for dt in m})
print("=" * 72)
print(f"法人動向訊號驗證(拆穿模式)  價格模式 {config.PRICE_MODE}")
print(f"kline {cal[0]}~{cal[-1]} {len(cal)}交易日 {len(data.d)}檔  "
      f"t86 {t86_dates[0]}~{t86_dates[-1]} {len(t86_dates)}天 {len(inst)}檔")
print(f"成本來回 {config.COST*100:.1f}%  基準=全市場等權")
print("=" * 72)
print("⚠️ t86 起 2021-06 → 樣本僅約 " f"{len(t86_dates)/244:.1f} 年,單一多頭為主 regime,"
      "關5 污染警示必看。")

# inst[sid][date] = (投信, 外資);idx 0=投信、1=外資
IDX_TRUST, IDX_FOREIGN = 0, 1


def t86_at(sid, i):
    """cal index i 當日 t86;無資料回 None。"""
    if i < 0:
        return None
    return inst.get(sid, {}).get(cal[i])


def streak_buy(sid, i, idx):
    """從 i-1 往回數連續淨買日數;缺資料即中斷(不補零)。"""
    n, j = 0, i - 1
    while j >= 0:
        v = t86_at(sid, j)
        if not v or v[idx] <= 0:
            break
        n += 1
        j -= 1
    return n


def streak_sell(sid, i, idx):
    """從 i 往回數連續淨賣日數;缺資料即中斷。"""
    n, j = 0, i
    while j >= 0:
        v = t86_at(sid, j)
        if not v or v[idx] >= 0:
            break
        n += 1
        j -= 1
    return n


# ---------- 7 個訊號(回 score / None;binary 訊號回 1.0,全取) ----------
def h1_factory(n_days):
    def sig(sid, date, c):
        i = cidx.get(date)
        if i is None:
            return None
        return 1.0 if streak_buy(sid, i, IDX_FOREIGN) >= n_days else None
    return sig


def h2_factory(n_days):
    def sig(sid, date, c):
        i = cidx.get(date)
        if i is None:
            return None
        return 1.0 if streak_buy(sid, i, IDX_TRUST) >= n_days else None
    return sig


def h3_flip(sid, date, c):
    """前 ≥3 日外資連賣、最新一日(dt-1)翻淨買。"""
    i = cidx.get(date)
    if i is None:
        return None
    v = t86_at(sid, i - 1)
    if not v or v[IDX_FOREIGN] <= 0:
        return None
    return 1.0 if streak_sell(sid, i - 2, IDX_FOREIGN) >= 3 else None


def h4_both(sid, date, c):
    i = cidx.get(date)
    if i is None:
        return None
    v = t86_at(sid, i - 1)
    if v and v[IDX_TRUST] > 0 and v[IDX_FOREIGN] > 0:
        return 1.0
    return None


def h5_strength(sid, date, c):
    """外資淨買股數 / 20 日均量 ≥ 10%(量取 dt-1 往回 20 個交易日)。"""
    i = cidx.get(date)
    if i is None or i < 21:
        return None
    v = t86_at(sid, i - 1)
    if not v or v[IDX_FOREIGN] <= 0:
        return None
    V = data.V.get(sid, {})
    vols = [V[cal[j]] for j in range(i - 21, i - 1) if V.get(cal[j])]
    if len(vols) < 15:
        return None
    avg = statistics.mean(vols)
    if avg <= 0:
        return None
    ratio = v[IDX_FOREIGN] / avg
    return ratio if ratio >= 0.10 else None


SIGNALS = [
    ("H1 外資連買≥3", h1_factory(3)),
    ("H1 外資連買≥5", h1_factory(5)),
    ("H2 投信連買≥3", h2_factory(3)),
    ("H2 投信連買≥5", h2_factory(5)),
    ("H3 外資轉向買", h3_flip),
    ("H4 法人合力", h4_both),
    ("H5 買超強度≥10%", h5_strength),
]
HOLDS = (5, 20)


def gate_cell(name, sig, hold):
    """一格 = 五關全跑(仿 engine.validate,重用同一份 data/ctx)。"""
    print("\n" + "-" * 72)
    print(f"◆ {name}  持有{hold}日")
    pairs, perdate = run(sig, data, ctx, hold=hold, rebalance=hold + 1,
                         top_pct=1.0, cost=config.COST)
    real = [v for _, v in pairs]
    if not real:
        print("  無樣本(訊號太少或資料不足)。")
        return None
    print("關1+2 公平基準 + 樣本外(切2024)")
    gates.report(name, pairs, monthly=(hold == 20))
    print("關2b 真·樣本外(切2025)")
    gates.report(name, pairs, monthly=(hold == 20), oos2=True)

    print("關3 隨機對照(同條件亂選)")
    rng = random.Random(42)

    def draw():
        out = []
        for dt, rets, k, ew in perdate:
            if len(rets) >= k:
                out.append((statistics.mean(rng.sample(rets, k)) - config.COST) - ew)
        return out

    p_rand = gates.random_control(real, draw)
    boot = gates.report_boot("bootstrap 顯著性", real)

    print("關4 成本敏感")
    for cc in (config.COST_LO, config.COST, config.COST_HI):
        shifted = [v - (cc - config.COST) for v in real]
        n, m, pos = gates.stats(shifted)
        tag = " ←現用" if abs(cc - config.COST) < 1e-9 else ""
        print(f"  成本{cc*100:.1f}%: 超額均={m*100:+.2f}% 勝率={pos:.0f}%{tag}")

    return {"name": name, "hold": hold, "pairs": pairs,
            "mean": statistics.mean(real), "n": len(real),
            "p_rand": p_rand, "boot": boot}


results = []
for name, sig in SIGNALS:
    for hold in HOLDS:
        results.append(gate_cell(name, sig, hold))

print("\n" + "=" * 72)
print("關5 regime 污染檢查(全格共用)")
gates.regime_health(data)

# ---------- 總表 ----------
print("\n" + "=" * 72)
print("14 格總表(超額 = 對全市場等權;判死線:年化超額<2% 或 p>0.05)")
print(f"{'訊號':<18}{'H':>3}{'n':>5}{'每期超額':>9}{'年化≈':>8}{'p(boot)':>9}{'隨機p':>7}  判定")
per_year = {5: 244 / 6, 20: 244 / 21}   # rebalance = hold+1
for r in results:
    if r is None:
        continue
    ann = (1 + r["mean"]) ** per_year[r["hold"]] - 1
    p = r["boot"][3] if r["boot"] else 1.0
    pr = f"{r['p_rand']:.0%}" if r["p_rand"] is not None else "-"
    dead = r["mean"] <= 0 or p > 0.05 or ann < 0.02
    tag = "💀 死" if dead else "🟡 需人工核關5"
    print(f"{r['name']:<18}{r['hold']:>3}{r['n']:>5}{r['mean']*100:>+8.2f}%"
          f"{ann*100:>+7.1f}%{p:>9.4f}{pr:>7}  {tag}")

print(f"\n耗時 {time.time()-t0:.0f}s")
