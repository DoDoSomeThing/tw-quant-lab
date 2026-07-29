#!/usr/bin/env python3
"""
回推搜尋引擎 —— 掃大量條件組合找「可能有勝率」的訊號,同時擋住 data snooping。

核心問題:掃 1000 組條件,光靠運氣就有 ~50 組會「p<0.05 顯著」。
單看每一組都過五關,但「我掃了幾組」沒被計入 → 五關失效,假 edge 就這樣生出來。

本模組加的是第六關 —— **Best-of-N 置換檢定(max-statistic / Westfall-Young)**:
  把同一套搜尋跑在「打亂的資料」上 N 次(每個再平衡日內,把前向報酬隨機重指派給股票,
  破壞訊號↔報酬關係,但保留日期結構、橫斷面分布、樣本數),
  記錄每次「亂掃能掃到的最好成績」→ 得到「純運氣最好能掃到多好」的分布。
  真實最佳沒贏過這個分布 → 就是噪音,不管它單獨看多漂亮。

問的不是「這組好不好」,是「**亂掃也能掃到這麼好嗎**」。

搜尋一律只在 IS(樣本內)做;OOS 由呼叫端鎖住,凍結後只准跑一次。
需要 numpy(只有本模組要)。
"""
import bisect
import statistics

import numpy as np

from framework import config

RNG = np.random.default_rng(42)


# ============ 面板:一次算好所有特徵 + 前向報酬 ============
def build_panel(data, revenue=None, inst=None, rebalance=21, warmup=252,
                holds=(10, 20, 40, 60), is_end=None):
    """
    回 panel dict(全部 numpy 陣列,每列 = 一個(股票, 再平衡日)):
      date_i   該列的再平衡日在 cal 的 index
      feat     {名稱: 陣列}  point-in-time 特徵
      fwd      {h: 前向報酬}
      ew       {h: 該列日期的全市場等權前向報酬}
      dates    每列的日期字串
    is_end:只收 date < is_end 的列(樣本內)。None=全收。
    """
    cal, C = data.cal, data.C
    bench = data.bench
    maxh = max(holds)

    # 每檔對齊主日曆的收盤(缺值 None)
    px = {}
    for sid in data.d:
        if sid == bench:
            continue
        cs = C[sid]
        px[sid] = [cs.get(dt) for dt in cal]

    # 營收 YoY:每檔 (avail_date, yoy) 排序序列,查詢時 bisect 取最新一筆 <= date
    yoy_seq = {}
    if revenue:
        for sid, rows in revenue.items():
            by_ym = {r[1]: r[2] for r in rows if r[2]}
            seq = []
            for avail, ym, r in sorted(rows, key=lambda x: x[0]):
                if not r:
                    continue
                prev = by_ym.get(ym - 100)
                if prev and prev > 0:
                    seq.append((avail, r / prev - 1))
            if seq:
                yoy_seq[sid] = ([a for a, _ in seq], [v for _, v in seq])

    reb = [i for i in range(warmup, len(cal) - maxh, rebalance)]
    if is_end:
        reb = [i for i in reb if cal[i] < is_end]

    EW = {h: data.build_ew(h) for h in holds}

    rows_date_i, rows_dates, rows_sid = [], [], []
    F = {k: [] for k in ("mom20", "mom60", "mom120", "mom252",
                         "px_ma20", "px_ma60", "px_ma120", "vol60", "yoy",
                         "inst", "volrank", "regime")}
    FW = {h: [] for h in holds}
    EWv = {h: [] for h in holds}

    # 規模分位(全期平均量的百分位)
    vols = sorted(data.avgvol.values())
    def volrank(sid):
        v = data.avgvol.get(sid, 0)
        return bisect.bisect_left(vols, v) / max(1, len(vols))
    vr = {sid: volrank(sid) for sid in px}

    for i in reb:
        dt = cal[i]
        reg = data.regime.get(dt)
        reg_v = 1.0 if reg is True else (0.0 if reg is False else np.nan)
        for sid, p in px.items():
            p0 = p[i]
            if not p0 or p0 <= 0:
                continue
            # 前向報酬(所有 hold 都要有)
            fwds = {}
            ok = True
            for h in holds:
                p1 = p[i + h] if i + h < len(p) else None
                if not p1 or p1 <= 0 or dt not in EW[h]:
                    ok = False
                    break
                fwds[h] = p1 / p0 - 1
            if not ok:
                continue
            # 動能(point-in-time:只用 i 以前)
            def mom(lb):
                q = p[i - lb] if i - lb >= 0 else None
                return (p0 / q - 1) if q and q > 0 else np.nan
            # 移動均(窗內有值的平均)
            def ma_ratio(n):
                w = [x for x in p[i - n + 1:i + 1] if x and x > 0]
                return (p0 / (sum(w) / len(w))) if len(w) >= n * 0.8 else np.nan
            w60 = [x for x in p[i - 59:i + 1] if x and x > 0]
            if len(w60) >= 48:
                rets = [w60[j] / w60[j - 1] - 1 for j in range(1, len(w60))]
                v60 = statistics.pstdev(rets) if len(rets) > 1 else np.nan
            else:
                v60 = np.nan
            # 營收 YoY(最新一筆 avail <= dt)
            y = np.nan
            s = yoy_seq.get(sid)
            if s:
                k = bisect.bisect_right(s[0], dt) - 1
                if k >= 0:
                    y = s[1][k]
            # 法人:前一交易日投信或外資淨買
            ib = 0.0
            if inst and i > 0:
                v = inst.get(sid, {}).get(cal[i - 1])
                ib = 1.0 if (v and (v[0] > 0 or v[1] > 0)) else 0.0

            rows_date_i.append(i)
            rows_dates.append(dt)
            rows_sid.append(sid)
            F["mom20"].append(mom(20)); F["mom60"].append(mom(60))
            F["mom120"].append(mom(120)); F["mom252"].append(mom(252))
            F["px_ma20"].append(ma_ratio(20)); F["px_ma60"].append(ma_ratio(60))
            F["px_ma120"].append(ma_ratio(120))
            F["vol60"].append(v60); F["yoy"].append(y)
            F["inst"].append(ib); F["volrank"].append(vr[sid]); F["regime"].append(reg_v)
            for h in holds:
                FW[h].append(fwds[h])
                EWv[h].append(EW[h][dt])

    return {
        "date_i": np.array(rows_date_i),
        "dates": np.array(rows_dates),
        "sid": np.array(rows_sid),
        "feat": {k: np.array(v, dtype=float) for k, v in F.items()},
        "fwd": {h: np.array(v, dtype=float) for h, v in FW.items()},
        "ew": {h: np.array(v, dtype=float) for h, v in EWv.items()},
        "holds": list(holds),
        "n_dates": len(reb),
    }


# ============ 條件字典(每維幾個互斥選項,含「不管」)============
def build_predicates(panel):
    """回 {維度: [(標籤, 布林遮罩 or None=不管), ...]}。"""
    f = panel["feat"]
    med_vol = np.nanmedian(f["vol60"])
    return {
        "size": [("不管", None),
                 ("大型(量前50%)", f["volrank"] >= 0.5),
                 ("中小(量後50%)", f["volrank"] < 0.5)],
        "regime": [("不管", None),
                   ("只非空頭", f["regime"] == 1.0)],
        "trend": [("不管", None),
                  ("站上MA60", f["px_ma60"] > 1.0),
                  ("站上MA120", f["px_ma120"] > 1.0)],
        "mom": [("不管", None),
                ("60日動能>0", f["mom60"] > 0),
                ("252日動能>0", f["mom252"] > 0)],
        "vol": [("不管", None),
                ("低波(<中位)", f["vol60"] < med_vol),
                ("高波(>=中位)", f["vol60"] >= med_vol)],
        "rev": [("不管", None),
                ("營收YoY>0", f["yoy"] > 0),
                ("營收YoY>20%", f["yoy"] > 0.2)],
        "inst": [("不管", None),
                 ("法人淨買", f["inst"] == 1.0)],
    }


# ============ 單組評估 ============
def evaluate(panel, mask, h, fwd=None, cost=config.COST, min_n=100, min_dates=10):
    """
    回 (score, n, winrate, n_dates) 或 None(樣本不足)。
    score = 每 20 日標準化超額(不同 hold 才可比):excess * 20/h
    excess = (前向報酬 - 成本) - 同日同期全市場等權
    """
    if mask is None:
        idx = np.arange(len(panel["dates"]))
    else:
        idx = np.flatnonzero(mask)
    if len(idx) < min_n:
        return None
    fw = (panel["fwd"][h] if fwd is None else fwd)[idx]
    ex = (fw - cost) - panel["ew"][h][idx]
    ex = ex[~np.isnan(ex)]
    if len(ex) < min_n:
        return None
    nd = len(np.unique(panel["dates"][idx]))
    if nd < min_dates:
        return None
    return float(ex.mean() * 20.0 / h), int(len(ex)), float((ex > 0).mean() * 100), nd


# ============ 全網格掃描 ============
def scan(panel, preds, cost=config.COST, min_n=100, fwd_override=None):
    """
    掃所有組合 × 所有 hold。回 list[dict],按 score 由大到小。
    fwd_override:{h: 打亂後的前向報酬} — 置換檢定用。
    """
    import itertools
    dims = list(preds.keys())
    out = []
    for choice in itertools.product(*[preds[d] for d in dims]):
        masks = [m for _, m in choice if m is not None]
        if masks:
            mask = np.logical_and.reduce(masks)
            if mask.sum() < min_n:
                continue
        else:
            mask = None
        labels = {d: lab for d, (lab, _) in zip(dims, choice)}
        for h in panel["holds"]:
            fw = fwd_override[h] if fwd_override else None
            r = evaluate(panel, mask, h, fwd=fw, cost=cost, min_n=min_n)
            if r:
                out.append({"labels": labels, "hold": h, "score": r[0],
                            "n": r[1], "win": r[2], "n_dates": r[3]})
    out.sort(key=lambda x: -x["score"])
    return out


# ============ 第六關:Best-of-N 置換檢定 ============
def permute_fwd(panel, rng=RNG):
    """每個再平衡日內,把前向報酬隨機重指派給股票(破壞訊號↔報酬,保留日期結構)。"""
    out = {}
    dates = panel["dates"]
    order = np.argsort(dates, kind="stable")
    # 依日期分組的 index 區段
    groups = []
    start = 0
    for k in range(1, len(order) + 1):
        if k == len(order) or dates[order[k]] != dates[order[start]]:
            groups.append(order[start:k])
            start = k
    for h in panel["holds"]:
        arr = panel["fwd"][h].copy()
        for g in groups:
            arr[g] = arr[rng.permutation(g)]
        out[h] = arr
    return out


def best_of_n_test(panel, preds, real_best, trials=30, cost=config.COST,
                   min_n=100, verbose=True):
    """
    跑 trials 次「打亂後的完整搜尋」,收集每次的最佳 score。
    回 (p_value, null_bests)。p = 亂掃最佳 >= 真實最佳 的比例。
    """
    nulls = []
    for t in range(trials):
        fwd = permute_fwd(panel)
        res = scan(panel, preds, cost=cost, min_n=min_n, fwd_override=fwd)
        b = res[0]["score"] if res else -9
        nulls.append(b)
        if verbose:
            print(f"    置換 {t+1}/{trials}: 亂掃最佳 {b*100:+.2f}%", flush=True)
    nulls = np.array(nulls)
    p = float((nulls >= real_best).mean())
    return p, nulls


# ============ 鄰居穩定度 ============
def neighbors(panel, preds, best, cost=config.COST, min_n=100):
    """把最佳組每一維單獨換掉,看鄰居是否也正(孤峰=擬合)。回 list[(說明, score, n)]。"""
    out = []
    dims = list(preds.keys())
    for d in dims:
        for lab, m in preds[d]:
            if lab == best["labels"][d]:
                continue
            choice = []
            for dd in dims:
                sel = m if dd == d else dict(preds[dd])[best["labels"][dd]]
                if sel is not None:
                    choice.append(sel)
            mask = np.logical_and.reduce(choice) if choice else None
            r = evaluate(panel, mask, best["hold"], cost=cost, min_n=min_n)
            out.append((f"{d}: {best['labels'][d]} → {lab}",
                        r[0] if r else None, r[1] if r else 0))
    # 持有期鄰居
    for h in panel["holds"]:
        if h == best["hold"]:
            continue
        choice = [dict(preds[dd])[best["labels"][dd]] for dd in dims]
        choice = [c for c in choice if c is not None]
        mask = np.logical_and.reduce(choice) if choice else None
        r = evaluate(panel, mask, h, cost=cost, min_n=min_n)
        out.append((f"hold: {best['hold']}日 → {h}日", r[0] if r else None, r[1] if r else 0))
    return out


# ============ 第七關:基準假象 + 集中度/聚類 ============
def peer_excess(panel, h, n_buckets=5, cost=config.COST):
    """
    同日 × 同規模桶 的 peer 相對超額。
    用途:全市場等權含一堆微型股,期間拉長「大型/優質」自然贏它 → 那是規模因子傾斜不是 alpha。
    換成 peer 基準,規模傾斜就被抵銷掉。
    """
    f, dates = panel["feat"], panel["dates"]
    fw = panel["fwd"][h]
    bucket = np.clip((f["volrank"] * n_buckets).astype(int), 0, n_buckets - 1)
    ex = np.full(len(fw), np.nan)
    for dt in np.unique(dates):
        dm = dates == dt
        for b in range(n_buckets):
            sel = dm & (bucket == b)
            if sel.sum() >= 5:
                ex[sel] = (fw[sel] - cost) - np.nanmean(fw[sel])
    return ex


def concentration(panel, mask, h, ex=None, cost=config.COST):
    """
    集中度檢查 —— 這個 edge 是「多數標的普遍有效」還是「少數幾檔撐起來」?

    置換檢定擋不住這個:它把報酬在日期內重新指派,等於打散「同一批股票重複中選」
    的結構,所以會把「一次產業/主題押注」判成真訊號。這關專門抓它。

    回 dict:
      n_rows/n_stocks/n_dates  樣本結構(重疊倍數 = n_rows/n_dates 看得出來)
      date_pos/date_median     日期層面:幾成為正、中位
      stock_pos/stock_median   股票層面:幾成為正、中位  ← 中位為負 = 少數撐起來
      top5_share/top10_share   前 N 檔佔總貢獻比例
      drop_top3                拿掉最好 3 天後還剩多少
      top_contrib              [(sid, 貢獻)] 前 10
    """
    if ex is None:
        ex = peer_excess(panel, h, cost=cost)
    idx = np.flatnonzero(mask & ~np.isnan(ex))
    if len(idx) < 30:
        return None
    ss, vv, dd = panel["sid"][idx], ex[idx], panel["dates"][idx]

    uniq_d = sorted(set(dd.tolist()))
    dvals = np.array([vv[dd == d].mean() for d in uniq_d])
    dw = np.array([(dd == d).sum() for d in uniq_d], dtype=float)

    uniq_s = sorted(set(ss.tolist()))
    svals = np.array([vv[ss == s].mean() for s in uniq_s])
    scontrib = np.array([vv[ss == s].sum() for s in uniq_s])

    order = np.argsort(-dvals)
    keep = np.ones(len(dvals), bool)
    keep[order[:3]] = False
    drop3 = float(np.average(dvals[keep], weights=dw[keep])) if keep.sum() else float("nan")

    so = np.argsort(-scontrib)
    tot = scontrib.sum()
    return {
        "n_rows": len(idx), "n_stocks": len(uniq_s), "n_dates": len(uniq_d),
        "mean": float(vv.mean()), "norm20": float(vv.mean() * 20.0 / h),
        "date_pos": float((dvals > 0).mean() * 100), "date_median": float(np.median(dvals)),
        "stock_pos": float((svals > 0).mean() * 100), "stock_median": float(np.median(svals)),
        "top5_share": float(scontrib[so[:5]].sum() / tot * 100) if tot else float("nan"),
        "top10_share": float(scontrib[so[:10]].sum() / tot * 100) if tot else float("nan"),
        "drop_top3": drop3, "drop_top3_norm20": drop3 * 20.0 / h,
        "top_contrib": [(uniq_s[i], float(scontrib[i])) for i in so[:10]],
    }


def report_concentration(c, indent="  "):
    """印集中度報告 + 判決。"""
    if not c:
        print(f"{indent}樣本不足")
        return False
    print(f"{indent}樣本結構:{c['n_rows']} 列 = {c['n_stocks']} 檔 × {c['n_dates']} 個日期"
          f"(每檔平均出現 {c['n_rows']/c['n_stocks']:.1f} 次)")
    print(f"{indent}vs 同規模 peer:{c['norm20']*100:+.2f}%/20日")
    print(f"{indent}日期層面:正 {c['date_pos']:.0f}%  中位 {c['date_median']*100:+.2f}%"
          f"   拿掉最好3天 → {c['drop_top3_norm20']*100:+.2f}%/20日")
    print(f"{indent}股票層面:正 {c['stock_pos']:.0f}%  中位 {c['stock_median']*100:+.2f}%")
    print(f"{indent}貢獻集中:前5檔佔 {c['top5_share']:.0f}%、前10檔佔 {c['top10_share']:.0f}%")
    print(f"{indent}  最大貢獻:{', '.join(s for s, _ in c['top_contrib'][:6])}")

    bad = []
    if c["stock_median"] <= 0:
        bad.append("多數個股其實是負的(中位≤0)")
    if c["top5_share"] >= 40:
        bad.append(f"前5檔就佔 {c['top5_share']:.0f}% 貢獻")
    if c["drop_top3_norm20"] < c["norm20"] * 0.5:
        bad.append("拿掉最好3天後腰斬")
    if c["n_rows"] / max(1, c["n_dates"]) > 1 and c["n_dates"] < 30:
        bad.append(f"只有 {c['n_dates']} 個日期(重疊持有→有效樣本更少)")
    if bad:
        print(f"{indent}🔴 判決:少數標的/少數時點撐起來的,不是普遍有效的選股 edge。")
        for b in bad:
            print(f"{indent}   - {b}")
        return False
    print(f"{indent}🟢 判決:多數標的與時點普遍有效,不是集中押注。")
    return True


def describe(item):
    on = [f"{v}" for k, v in item["labels"].items() if v != "不管"]
    return (" × ".join(on) if on else "(全市場,無條件)") + f"  [持有{item['hold']}日]"
