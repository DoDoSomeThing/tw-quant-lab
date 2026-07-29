#!/usr/bin/env python3
"""
校準式判定 —— 把「門檻」從人訂的數字,換成資料自己算出來的分布。

問題:先前第七關用的門檻(前5檔≥40%、日期<30、拿掉3天腰斬)是**人訂的**,
沒有依據。訂太嚴 → 什麼都判死;訂太鬆 → 什麼都放行。兩種都沒有價值。

解法:對每一個統計量,都拿「**同規模、同日期結構的隨機選股**」當對照組。
  - 隨機組每天抽的檔數 = 真實訊號那天選的檔數(完全一樣的結構)
  - 抽 n_random 次 → 得到該統計量在「沒有任何選股能力」下的**實測分布**
  - 真實值報成**百分位**與**單尾經驗 p 值**

這樣就沒有「我覺得 40% 太集中」這種話。只有「真實集中度落在隨機分布的第 X 百分位」。
例如:隨機選 100 檔,前5檔本來就會佔獲利的一大半 —— 這是資料告訴我們的,不是我猜的。

── 事前登記的判定規則(寫在跑之前,不因結果調整)────────────────────
  唯一的自由參數是顯著水準 α = 0.05(統計慣例,非為此問題發明)。
  訊號「通過」需同時滿足:
    (A) 效果量:超額報酬百分位 >= 95(即經驗 p <= 0.05,單尾)
    (B) 非集中:前5檔貢獻佔比**不高於**隨機分布的中位數(百分位 <= 50)
    (C) 非少數時點:**拿掉最好 3 個日期後**的超額,百分位仍 >= 95
        (即拔掉最強時點後,效果仍顯著贏過隨機 —— 與 (A) 同一把尺,不另訂標準)
  (B) 用隨機中位數當界線 —— 意思是「不比隨機選股更集中」,不是人訂數字。
  另報「偵測門檻」(見 studies/validator_selftest.py):效果量低於門檻時,
  未通過 = **無法判定**,不等於「無效」。
──────────────────────────────────────────────────────
"""
import numpy as np

from framework import config

RNG = np.random.default_rng(20260729)

# 唯一自由參數:標準顯著水準
ALPHA = 0.05


def matched_random_masks(panel, mask, n_random=200, rng=RNG):
    """
    產生 n_random 個「同規模、同日期結構」的隨機選股遮罩。
    每個日期抽的檔數 = 真實訊號在該日選的檔數,母體 = 該日面板上所有可選列。
    """
    dates = panel["dates"]
    idx_by_date = {}
    for dt in np.unique(dates):
        idx_by_date[dt] = np.flatnonzero(dates == dt)
    counts = {dt: int(mask[idx_by_date[dt]].sum()) for dt in idx_by_date}
    counts = {dt: c for dt, c in counts.items() if c > 0}

    out = []
    for _ in range(n_random):
        m = np.zeros(len(dates), dtype=bool)
        for dt, c in counts.items():
            pool = idx_by_date[dt]
            if c >= len(pool):
                m[pool] = True
            else:
                m[rng.choice(pool, size=c, replace=False)] = True
        out.append(m)
    return out


def _stats(panel, mask, h, ex):
    """回一組統計量(全部由資料算,無門檻)。樣本不足回 None。"""
    idx = np.flatnonzero(mask & np.isfinite(ex))
    if len(idx) < 30:
        return None
    vv = ex[idx]
    ss = panel["sid"][idx]
    dd = panel["dates"][idx]
    k = 20.0 / h

    uniq_d = np.unique(dd)
    dvals = np.array([vv[dd == d].mean() for d in uniq_d])
    dw = np.array([(dd == d).sum() for d in uniq_d], dtype=float)
    order = np.argsort(-dvals)
    keep = np.ones(len(dvals), bool)
    keep[order[:min(3, len(dvals))]] = False
    overall = float(np.average(dvals, weights=dw))
    # 「拿掉最好 3 個日期後的超額」——直接用絕對量,不用比值。
    # (先前版本用 drop3/overall 比值:分母是接近 0 或負的超額 → 比值會炸開,
    #  隨機對照的 95% 區間跑到 [-11, +7],等於用一個數學上無效的指標在判死訊號。)
    drop3 = float(np.average(dvals[keep], weights=dw[keep])) if keep.sum() else np.nan

    uniq_s = np.unique(ss)
    svals = np.array([vv[ss == s].mean() for s in uniq_s])
    scontrib = np.array([vv[ss == s].sum() for s in uniq_s])
    posc = scontrib[scontrib > 0].sum()
    so = np.argsort(-scontrib)
    top5 = float(scontrib[so[:5]].sum() / posc * 100) if posc > 0 else np.nan

    return {
        "excess20": float(vv.mean() * k),
        "stock_median": float(np.median(svals)),
        "stock_pos": float((svals > 0).mean() * 100),
        "date_pos": float((dvals > 0).mean() * 100),
        "top5_share": top5,
        "drop3_excess20": float(drop3 * k) if np.isfinite(drop3) else np.nan,
        "n": len(idx), "n_stocks": len(uniq_s), "n_dates": len(uniq_d),
    }


def calibrate(panel, mask, h, ex=None, n_random=200, cost=config.COST, rng=RNG):
    """
    回 dict:{統計量: {"real":值, "null_med":隨機中位, "pct":真實值在隨機分布的百分位}}
    外加 n/n_stocks/n_dates 與 pass_* 判定(依模組開頭事前登記的規則)。
    """
    from framework import search as S
    if ex is None:
        ex = S.peer_excess(panel, h, cost=cost)

    real = _stats(panel, mask, h, ex)
    if real is None:
        return None

    keys = ["excess20", "stock_median", "stock_pos", "date_pos", "top5_share", "drop3_excess20"]
    nulls = {k: [] for k in keys}
    for m in matched_random_masks(panel, mask, n_random=n_random, rng=rng):
        s = _stats(panel, m, h, ex)
        if s is None:
            continue
        for k in keys:
            if np.isfinite(s[k]):
                nulls[k].append(s[k])

    out = {"n": real["n"], "n_stocks": real["n_stocks"], "n_dates": real["n_dates"],
           "n_random": n_random, "metrics": {}}
    for k in keys:
        arr = np.array(nulls[k], dtype=float)
        if len(arr) == 0 or not np.isfinite(real[k]):
            out["metrics"][k] = {"real": real[k], "null_med": np.nan, "pct": np.nan}
            continue
        pct = float((arr < real[k]).mean() * 100)
        out["metrics"][k] = {"real": float(real[k]), "null_med": float(np.median(arr)),
                             "null_lo": float(np.percentile(arr, 2.5)),
                             "null_hi": float(np.percentile(arr, 97.5)), "pct": pct}

    m = out["metrics"]
    out["pass_effect"] = bool(np.isfinite(m["excess20"]["pct"]) and
                              m["excess20"]["pct"] >= (1 - ALPHA) * 100)
    out["pass_concentration"] = bool(np.isfinite(m["top5_share"]["pct"]) and
                                     m["top5_share"]["pct"] <= 50)
    out["pass_robust_dates"] = bool(np.isfinite(m["drop3_excess20"]["pct"]) and
                                    m["drop3_excess20"]["pct"] >= (1 - ALPHA) * 100)
    out["passed"] = bool(out["pass_effect"] and out["pass_concentration"]
                         and out["pass_robust_dates"])
    return out


LABELS = {
    "excess20": "超額報酬/20日",
    "stock_median": "個股平均的中位數",
    "stock_pos": "個股為正比例%",
    "date_pos": "日期為正比例%",
    "top5_share": "前5檔佔總獲利%",
    "drop3_excess20": "去最好3天後超額/20日",
}


def report(c, indent="  ", title=None):
    """把校準結果印出來。只印數字與百分位,不下形容詞。"""
    if not c:
        print(f"{indent}樣本不足")
        return False
    if title:
        print(f"{indent}{title}")
    print(f"{indent}樣本:{c['n']} 列 = {c['n_stocks']} 檔 × {c['n_dates']} 個日期"
          f"   對照:{c['n_random']} 組同規模隨機選股")
    print(f"{indent}{'統計量':<20}{'真實':>10}{'隨機中位':>10}{'隨機95%區間':>20}{'百分位':>8}")
    for k, lab in LABELS.items():
        m = c["metrics"][k]
        if not np.isfinite(m["pct"]):
            print(f"{indent}{lab:<20}{'—':>10}")
            continue
        scale = 100 if k in ("excess20", "stock_median", "drop3_excess20") else 1
        print(f"{indent}{lab:<20}{m['real']*scale:>9.2f}{'%' if scale == 100 else ' '}"
              f"{m['null_med']*scale:>9.2f}{'%' if scale == 100 else ' '}"
              f"  [{m['null_lo']*scale:>7.2f},{m['null_hi']*scale:>7.2f}]"
              f"{m['pct']:>7.1f}")
    print(f"{indent}判定(事前登記規則,α={ALPHA}):"
          f" 效果量{'✅' if c['pass_effect'] else '❌'}"
          f" 非集中{'✅' if c['pass_concentration'] else '❌'}"
          f" 非少數時點{'✅' if c['pass_robust_dates'] else '❌'}"
          f"  → {'通過' if c['passed'] else '未通過'}")
    return c["passed"]
