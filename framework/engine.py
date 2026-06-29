#!/usr/bin/env python3
"""
五關引擎 —— 吃一個訊號函式,自動跑完五關出報告。

    from framework.engine import validate
    validate(my_signal, "我的訊號")

訊號函式介面(見 context.py):
    def my_signal(sid, date, ctx) -> float | None: ...

驗證流程(橫斷面、月再平衡):每個再平衡日對全市場算訊號分數,
取前 top_pct(預設前10%),等權持有 hold 日,超額 = 組合報酬 - 全市場等權。
然後自動跑:
  關1 公平基準(vs 等權)        關2 真OOS(2024 / 2025 兩刀)
  關3 隨機對照(贏不過亂選=沒料) 關4 成本敏感(線性平移,免重算)
  關5 regime 分層 + 污染警示
"""
import random
import statistics

from framework import config
from framework.data import Data, load_revenue, load_t86
from framework.context import Context
from framework import gates

random.seed(42)


def run(signal, data, ctx, hold=20, rebalance=21, top_pct=0.10,
        min_pick=5, pick_top=True, regime_gate=False, cost=config.COST, warmup=252):
    """
    回 (pairs, perdate)。
      pairs   = [(date, 超額)]  每個再平衡日一筆
      perdate = [(date, [全市場 fwd 報酬...], k, ew)]  供隨機對照從全市場重抽
    """
    EW = data.build_ew(hold)
    cal, C, bench = data.cal, data.C, data.bench
    pairs, perdate = [], []
    for i in range(warmup, len(cal) - hold, rebalance):
        dt = cal[i]
        if regime_gate and not data.regime.get(dt):
            pairs.append((dt, 0.0))
            continue
        if dt not in EW:
            continue
        fwd = cal[i + hold]
        cand = []         # (score, fwd_ret) 通過訊號的
        all_rets = []     # 全市場 fwd_ret(隨機對照的 null 母體)
        for sid in data.d:
            if sid == bench:
                continue
            ce = C[sid].get(dt)
            cx = C[sid].get(fwd)
            if not ce or not cx or ce <= 0:
                continue
            ret = cx / ce - 1
            all_rets.append(ret)
            s = signal(sid, dt, ctx)
            if s is not None:
                cand.append((s, ret))
        if len(cand) < min_pick * 2:
            continue
        cand.sort(key=lambda t: t[0], reverse=pick_top)
        k = max(min_pick, int(len(cand) * top_pct))
        port = statistics.mean(r for _, r in cand[:k])
        pairs.append((dt, (port - cost) - EW[dt]))
        perdate.append((dt, all_rets, k, EW[dt]))
    return pairs, perdate


def validate(signal, name="訊號", data=None, hold=20, rebalance=21, top_pct=0.10,
             pick_top=True, regime_gate=False, cost=config.COST,
             with_revenue=True, with_inst=False):
    """跑完五關並印報告。回 pairs(供進一步分析)。"""
    if data is None:
        data = Data()
    rev = load_revenue() if with_revenue else None
    inst = load_t86() if with_inst else None
    ctx = Context(data, revenue=rev, inst=inst)

    print("=" * 72)
    print(f"訊號驗證:{name}")
    print(f"資料 {data.cal[0]}~{data.cal[-1]}  {len(data.cal)}交易日  {len(data.d)}檔  "
          f"持有{hold}日 再平衡{rebalance}日 取前{top_pct*100:.0f}% 成本{cost*100:.1f}%"
          + ("  [regime gate]" if regime_gate else ""))
    print("=" * 72)

    pairs, perdate = run(signal, data, ctx, hold=hold, rebalance=rebalance,
                         top_pct=top_pct, pick_top=pick_top,
                         regime_gate=regime_gate, cost=cost)
    real = [v for _, v in pairs]
    if not real:
        print("  無樣本(訊號太少或資料不足)。")
        return pairs

    print("\n關1+2 公平基準 + 樣本外(IS/OOS 切 2024)")
    gates.report(name, pairs, monthly=True)
    print("\n關2b 真·樣本外(IS/OOS 切 2025)")
    gates.report(name, pairs, monthly=True, oos2=True)

    print("\n關3 隨機對照(同條件亂選)")

    def draw():
        out = []
        for dt, rets, k, ew in perdate:
            if len(rets) >= k:
                samp = random.sample(rets, k)
                out.append((statistics.mean(samp) - cost) - ew)
        return out

    gates.random_control(real, draw)
    gates.report_boot("bootstrap 顯著性", real)

    print("\n關4 成本敏感(來回成本)")
    # excess 對 cost 線性:改成本 = 整體平移,免重算訊號
    for c in (config.COST_LO, cost, config.COST_HI):
        shifted = [v - (c - cost) for v in real]
        n, m, pos = gates.stats(shifted)
        tag = " ←現用" if abs(c - cost) < 1e-9 else ""
        print(f"  成本{c*100:.1f}%: 超額均={m*100:+.2f}% 勝率={pos:.0f}%{tag}")

    print("\n關5 regime 污染檢查")
    gates.regime_health(data)

    print("\n判讀:五關全綠才算 edge —— 超額穩定為正、OOS(尤其2025)不崩、"
          "贏得過隨機、成本拉高不翻負、且樣本期非全極端 regime。")
    return pairs
