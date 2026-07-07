#!/usr/bin/env python3
"""
五關引擎 —— 誠實驗證器的心臟。

  關1 公平基準   excess = 個股報酬 - 全市場等權(別用 0050 自欺)
  關2 真樣本外   切日期,新年份不可碰(IS / OOS / OOS2=2025)
  關3 隨機對照   贏不過亂選 = 沒料(bootstrap 經驗 p 值)
  關4 成本敏感   來回成本拉高會不會翻負(study 用不同 cost 重跑)
  關5 regime分層 分年 + 多空 + 污染警示(樣本期是否全是極端 regime)

這裡放「算 + 印」的通用工具;每個 study 用同一套,數字才可比、報告才一致。
"""
import random
import statistics

from framework import config
from framework.data import yr

# 不用全域 seed：同一進程跑多個 study 時,全域 RNG 狀態會讓結果依「呼叫順序」而變。
# 每個需要隨機的函式各自建 local Random(seed) → 同輸入永遠同輸出,順序無關。


# ============ 關 1+2+5:超額報酬分層報告 ============
def stats(vals):
    """[超額值] -> (n, 均值, 勝率%)。"""
    n = len(vals)
    if n == 0:
        return 0, 0.0, 0.0
    return n, statistics.mean(vals), sum(1 for v in vals if v > 0) / n * 100


def report(name, pairs, monthly=False, oos_from=None, oos2=False, indent="  "):
    """
    pairs = [(date, excess)] 印一行五關摘要(關1/2/5)。
      monthly=True  加年化超額 + Sharpe(月再平衡用)
      oos2=True     IS/OOS 切點改 OOS2_FROM(2025 真樣本外)
    """
    pairs = [(dt, v) for dt, v in pairs if v is not None]
    if not pairs:
        print(f"{indent}{name}: 無樣本")
        return
    cut = oos_from or (config.OOS2_FROM if oos2 else config.OOS_FROM)
    vals = [v for _, v in pairs]
    n, mean, pos = stats(vals)
    iss = [v for dt, v in pairs if dt < cut]
    oos = [v for dt, v in pairs if dt >= cut]
    by = {}
    for dt, v in pairs:
        by.setdefault(yr(dt), []).append(v)

    head = f"{indent}{name}: n={n} 超額均={mean*100:+.2f}% 勝率={pos:.0f}%"
    if monthly:
        sd = statistics.pstdev(vals) if n > 1 else 0
        sharpe = (mean / sd * (12 ** 0.5)) if sd > 0 else 0  # 月->年
        ann = (1 + mean) ** 12 - 1
        head += f" 年化超額={ann*100:+.1f}% Sharpe≈{sharpe:.2f}"
    print(head)
    if iss and oos:
        print(f"{indent}   IS(<{cut[:4]})均={statistics.mean(iss)*100:+.2f}%"
              f"  OOS(>={cut[:4]})均={statistics.mean(oos)*100:+.2f}%")
    print(f"{indent}   分年: " +
          "  ".join(f"{y}:{statistics.mean(v)*100:+.1f}%(n{len(v)})" for y, v in sorted(by.items())))


# ============ 關 3:隨機對照 + bootstrap ============
def bootstrap(xs, n=5000, seed=42):
    """回 (mean, ci_lo, ci_hi, p_two_sided)。純 python,無 scipy。local seed=可重現且與呼叫順序無關。"""
    if not xs:
        return None
    rng = random.Random(seed)
    m = statistics.mean(xs)
    N = len(xs)
    means = []
    for _ in range(n):
        s = sum(xs[rng.randrange(N)] for _ in range(N)) / N
        means.append(s)
    means.sort()
    lo = means[int(0.025 * n)]
    hi = means[int(0.975 * n)]
    frac_le0 = sum(1 for v in means if v <= 0) / n
    p = 2 * min(frac_le0, 1 - frac_le0)
    return round(m, 4), round(lo, 4), round(hi, 4), round(p, 4)


def verdict(p, mean):
    if mean <= 0:
        return "🔴 負期望(無 edge)"
    if p < 0.05:
        return "🟢 顯著為正(像真 edge)"
    return "🟡 帳面正但不顯著(無法排除運氣)"


def report_boot(name, xs, indent="  "):
    """印 bootstrap CI + p + 燈號(關3 統計面)。"""
    b = bootstrap(xs)
    if not b:
        print(f"{indent}{name}: 無資料")
        return None
    m, lo, hi, p = b
    print(f"{indent}{name}: n={len(xs)} 均值={m*100:+.2f}% "
          f"CI[{lo*100:+.2f},{hi*100:+.2f}] p={p} → {verdict(p, m)}")
    return b


def random_control(real_vals, draw_random, trials=300, indent="  "):
    """
    關3 核心:你的選股 vs 同條件亂選。
      real_vals    你的策略每筆報酬
      draw_random  callable()->[隨機選股每筆報酬](每次重抽一輪)
      trials       隨機輪數
    印「隨機 >= 你的」比例(經驗 p 值;>5% = 贏不過亂選)。
    """
    if not real_vals:
        print(f"{indent}無樣本")
        return
    real_mean = statistics.mean(real_vals)
    rand_means = []
    for _ in range(trials):
        rn = draw_random()
        if rn:
            rand_means.append(statistics.mean(rn))
    if not rand_means:
        print(f"{indent}隨機對照無樣本")
        return
    better = sum(1 for v in rand_means if v >= real_mean) / len(rand_means)
    print(f"{indent}你的選股均值 {real_mean*100:+.2f}%;{trials} 次隨機均值 "
          f"中位 {statistics.median(rand_means)*100:+.2f}%")
    print(f"{indent}隨機 >= 你的 = {better:.1%}(經驗 p 值;>5% = 贏不過亂選)")
    return better


# ============ 關 5:regime 污染警示 ============
def regime_health(data, indent="  "):
    """
    偵測樣本期是否全是極端 regime(台股 2021-25 無正常年 → edge 驗不出)。
    印每年 基準(0050)報酬 + 波動,標記極端年(|報酬|>35% 或年化波動>40%)。
    """
    cal, bc = data.cal, data.bc
    by_year = {}
    for i, dt in enumerate(cal):
        by_year.setdefault(yr(dt), []).append(bc[i])
    print(f"{indent}基準 {data.bench} 各年(污染檢查):")
    extreme = 0
    years = 0
    for y, closes in sorted(by_year.items()):
        if len(closes) < 20:
            continue
        years += 1
        ret = closes[-1] / closes[0] - 1
        rets = [closes[j] / closes[j - 1] - 1 for j in range(1, len(closes)) if closes[j - 1]]
        vol = statistics.pstdev(rets) * (252 ** 0.5) if len(rets) > 1 else 0
        flag = abs(ret) > 0.35 or vol > 0.40
        if flag:
            extreme += 1
        print(f"{indent}  {y}: 報酬 {ret*100:+6.1f}%  年化波動 {vol*100:5.1f}%"
              f"  {'⚠️ 極端' if flag else ''}")
    if years:
        frac = extreme / years
        print(f"{indent}極端年 {extreme}/{years} = {frac:.0%}")
        if frac >= 0.6:
            print(f"{indent}🚨 樣本期 {frac:.0%} 是極端 regime → 選股 edge 在這段驗不出,"
                  f"結論僅供參考(需更長/含正常年的歷史)。")
