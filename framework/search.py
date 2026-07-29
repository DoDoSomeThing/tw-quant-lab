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
def _aligned(bars, cal_idx, n, key):
    """把某檔的 bar 欄位對齊主日曆,缺值 = nan。"""
    a = np.full(n, np.nan)
    for b in bars:
        k = cal_idx.get(b["date"])
        if k is not None:
            v = b.get(key)
            if v:
                a[k] = v
    return a


def _stock_features(close, high, low, vol):
    """
    單檔的全部 point-in-time 特徵(pandas rolling,只用當下與過去)。
    回 {名稱: 陣列}(與輸入等長,不足期間 = nan)。
    """
    import pandas as pd
    c = pd.Series(close)
    h = pd.Series(high)
    lo = pd.Series(low)
    v = pd.Series(vol)

    out = {}
    # 動能(跳過最近 gap 的版本另計)
    for lb in (20, 60, 120, 252):
        out[f"mom{lb}"] = (c / c.shift(lb) - 1).to_numpy()
    # 均線乖離(價/MA)
    for n in (20, 60, 120):
        out[f"px_ma{n}"] = (c / c.rolling(n, min_periods=int(n * 0.8)).mean()).to_numpy()
    # 波動(60日日報酬標準差)
    out["vol60"] = (c.pct_change(fill_method=None)
                    .rolling(60, min_periods=48).std(ddof=0).to_numpy())
    # KD(9,3,3):K = RSV 的 1/3 指數平滑,D = K 的 1/3 指數平滑
    llv = lo.rolling(9, min_periods=9).min()
    hhv = h.rolling(9, min_periods=9).max()
    rsv = ((c - llv) / (hhv - llv) * 100).replace([np.inf, -np.inf], np.nan).fillna(50)
    K = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    D = K.ewm(alpha=1 / 3, adjust=False).mean()
    out["kd_k"] = K.to_numpy()
    out["kd_gold"] = ((K > D) & (K.shift(1) <= D.shift(1))).astype(float).to_numpy()
    # RSI(14, Wilder)
    diff = c.diff()
    up = diff.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-diff.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    out["rsi14"] = (100 - 100 / (1 + up / dn.replace(0, np.nan))).to_numpy()
    # 量能:近5日均量 / 近60日均量
    out["volratio"] = (v.rolling(5, min_periods=4).mean()
                       / v.rolling(60, min_periods=40).mean()).to_numpy()
    # 52週位置:0=一年最低、1=一年最高
    mn = c.rolling(252, min_periods=200).min()
    mx = c.rolling(252, min_periods=200).max()
    out["pos52"] = ((c - mn) / (mx - mn)).replace([np.inf, -np.inf], np.nan).to_numpy()
    return out


def _inst_series(inst_map, cal, sid):
    """法人:當日淨買(投信或外資>0)與「連續淨買天數」。回 (buy, streak) 陣列。"""
    n = len(cal)
    buy = np.zeros(n)
    m = inst_map.get(sid)
    if m:
        for k, dt in enumerate(cal):
            v = m.get(dt)
            if v and (v[0] > 0 or v[1] > 0):
                buy[k] = 1.0
    streak = np.zeros(n)
    run = 0.0
    for k in range(n):
        run = run + 1 if buy[k] else 0.0
        streak[k] = run
    return buy, streak


def build_panel(data, revenue=None, inst=None, rebalance=21, warmup=252,
                holds=(10, 20, 40, 60), is_end=None):
    """
    回 panel dict(全部 numpy 陣列,每列 = 一個(股票, 再平衡日)):
      sid/dates/date_i/date_code   身分與日期(date_code = 整數化日期,評估用)
      feat   {名稱: 陣列}  point-in-time 特徵(見 _stock_features + 營收/法人)
      fwd    {h: 前向報酬}      ew {h: 該列日期的全市場等權前向報酬}
    is_end:只收 date < is_end 的列(樣本內)。None=全收。

    注意:特徵一律只用「當日與過去」;前向報酬用 i → i+h,不會偷看。
    """
    cal, bench = data.cal, data.bench
    cal_idx = {dt: i for i, dt in enumerate(cal)}
    n = len(cal)
    maxh = max(holds)

    reb = [i for i in range(warmup, n - maxh, rebalance)]
    if is_end:
        reb = [i for i in reb if cal[i] < is_end]
    reb = np.array(reb, dtype=int)
    reb_dates = np.array([cal[i] for i in reb])

    EW = {h: data.build_ew(h) for h in holds}
    ew_at = {h: np.array([EW[h].get(cal[i], np.nan) for i in reb]) for h in holds}

    # 營收:每檔 (avail_date, yoy, accel) 排序序列
    rev_seq = {}
    if revenue:
        for sid, rows in revenue.items():
            by_ym = {r[1]: r[2] for r in rows if r[2]}
            avails, yoys, accels = [], [], []
            hist = {}
            for avail, ym, r in sorted(rows, key=lambda x: x[1]):
                if not r:
                    continue
                prev = by_ym.get(ym - 100)
                if not prev or prev <= 0:
                    continue
                y = r / prev - 1
                hist[ym] = y
                p3 = [hist[ym - 100 * k] for k in (1, 2, 3) if (ym - 100 * k) in hist]
                a = (y - statistics.mean(p3)) if len(p3) == 3 else np.nan
                avails.append(avail); yoys.append(y); accels.append(a)
            if avails:
                o = np.argsort(np.array(avails))
                rev_seq[sid] = (np.array(avails)[o], np.array(yoys)[o], np.array(accels)[o])

    # 規模分位
    vols_sorted = sorted(data.avgvol.values())

    feat_names = None
    SIDS, FEATS, DATES, DIDX = [], [], [], []
    FWD = {h: [] for h in holds}

    for sid, bars in data.d.items():
        if sid == bench:
            continue
        close = _aligned(bars, cal_idx, n, "close")
        p_reb = close[reb]
        base_ok = np.isfinite(p_reb) & (p_reb > 0)
        if base_ok.sum() == 0:
            continue
        high = _aligned(bars, cal_idx, n, "max")
        low = _aligned(bars, cal_idx, n, "min")
        volu = _aligned(bars, cal_idx, n, "volume")

        f = _stock_features(close, high, low, volu)
        row = {k: v[reb] for k, v in f.items()}

        # 規模分位(全期平均量的百分位,常數)
        vr = bisect.bisect_left(vols_sorted, data.avgvol.get(sid, 0)) / max(1, len(vols_sorted))
        row["volrank"] = np.full(len(reb), vr)

        # regime(依日期)
        row["regime"] = np.array([1.0 if data.regime.get(cal[i]) is True
                                  else (0.0 if data.regime.get(cal[i]) is False else np.nan)
                                  for i in reb])

        # 營收 YoY / 加速度(最新一筆 avail <= 該日)
        y_arr = np.full(len(reb), np.nan)
        a_arr = np.full(len(reb), np.nan)
        s = rev_seq.get(sid)
        if s is not None:
            pos = np.searchsorted(s[0], reb_dates, side="right") - 1
            ok = pos >= 0
            y_arr[ok] = s[1][pos[ok]]
            a_arr[ok] = s[2][pos[ok]]
        row["yoy"] = y_arr
        row["yoy_accel"] = a_arr

        # 法人(用前一交易日的資料,避免偷看當日盤後)
        if inst:
            buy, streak = _inst_series(inst, cal, sid)
            prev = np.clip(reb - 1, 0, n - 1)
            row["inst"] = buy[prev]
            row["inst_streak"] = streak[prev]
        else:
            row["inst"] = np.zeros(len(reb))
            row["inst_streak"] = np.zeros(len(reb))

        # 前向報酬
        fw = {}
        ok_all = base_ok.copy()
        for h in holds:
            nxt = close[np.clip(reb + h, 0, n - 1)]
            r = nxt / p_reb - 1
            good = np.isfinite(r) & np.isfinite(ew_at[h]) & (reb + h < n)
            fw[h] = np.where(good, r, np.nan)
            ok_all &= good

        if ok_all.sum() == 0:
            continue
        if feat_names is None:
            feat_names = sorted(row.keys())
        m = ok_all
        SIDS.append(np.full(int(m.sum()), sid))
        FEATS.append(np.column_stack([row[k][m] for k in feat_names]))
        for h in holds:
            FWD[h].append(fw[h][m])
        DATES.append(reb_dates[m])
        DIDX.append(reb[m])

    if not FEATS:
        raise SystemExit("面板為空。")

    F = np.vstack(FEATS)
    feat = {name: F[:, j] for j, name in enumerate(feat_names)}
    dates = np.concatenate(DATES)
    date_i = np.concatenate(DIDX)
    sid_arr = np.concatenate(SIDS)

    uniq_dates, date_code = np.unique(dates, return_inverse=True)
    ew_by_code = {h: np.array([EW[h].get(d, np.nan) for d in uniq_dates]) for h in holds}

    return {
        "sid": sid_arr,
        "dates": dates,
        "date_i": date_i,
        "date_code": date_code,
        "n_uniq_dates": len(uniq_dates),
        "feat": feat,
        "fwd": {h: np.concatenate(FWD[h]) for h in holds},
        "ew": {h: ew_by_code[h][date_code] for h in holds},
        "holds": list(holds),
        "n_dates": len(reb),
    }


# ============ 條件字典(每維幾個互斥選項,含「不管」)============
def build_predicates(panel, wide=True):
    """
    回 {維度: [(標籤, 布林遮罩 or None=不管), ...]}。
    wide=False → 只用原本 7 個核心維度(舊行為)。
    """
    f = panel["feat"]

    def q(name, p):
        return np.nanquantile(f[name], p)

    med_vol = q("vol60", 0.5)
    core = {
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
                ("252日動能>0", f["mom252"] > 0),
                ("20日回檔<0", f["mom20"] < 0)],
        "vol": [("不管", None),
                ("低波(<中位)", f["vol60"] < med_vol),
                ("高波(>=中位)", f["vol60"] >= med_vol)],
        "rev": [("不管", None),
                ("營收YoY>0", f["yoy"] > 0),
                ("營收YoY>20%", f["yoy"] > 0.2),
                ("營收加速>0", f["yoy_accel"] > 0)],
        "inst": [("不管", None),
                 ("法人淨買", f["inst"] == 1.0),
                 ("法人連買>=3日", f["inst_streak"] >= 3)],
    }
    if not wide:
        return core

    core.update({
        "kd": [("不管", None),
               ("KD超賣(K<20)", f["kd_k"] < 20),
               ("KD超買(K>80)", f["kd_k"] > 80),
               ("KD黃金交叉", f["kd_gold"] == 1.0)],
        "rsi": [("不管", None),
                ("RSI<30(超賣)", f["rsi14"] < 30),
                ("RSI>70(超買)", f["rsi14"] > 70)],
        "volume": [("不管", None),
                   ("量增(5/60>1.5)", f["volratio"] > 1.5),
                   ("量縮(5/60<0.7)", f["volratio"] < 0.7)],
        "pos52": [("不管", None),
                  ("逼近52週高(>90%)", f["pos52"] > 0.9),
                  ("逼近52週低(<20%)", f["pos52"] < 0.2)],
        "bias": [("不管", None),
                 ("乖離MA20>5%", f["px_ma20"] > 1.05),
                 ("乖離MA20<-5%", f["px_ma20"] < 0.95)],
    })
    return core


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
    ok = ~np.isnan(ex)
    ex = ex[ok]
    if len(ex) < min_n:
        return None
    # 用整數 date_code 數日期(比對字串 np.unique 快很多,掃描時是熱點)
    nd = int(np.bincount(panel["date_code"][idx][ok],
                         minlength=panel["n_uniq_dates"]).astype(bool).sum())
    if nd < min_dates:
        return None
    return float(ex.mean() * 20.0 / h), int(len(ex)), float((ex > 0).mean() * 100), nd


# ============ 全網格掃描 ============
def iter_combos(preds, max_active=4):
    """
    產生所有「最多 max_active 個生效條件」的組合。
    限制條件數不只是為了控制搜尋空間,也直接降低過擬合
    (條件越多越容易在樣本內雕出漂亮曲線)。
    回 [(labels dict, [遮罩...]), ...]。
    """
    import itertools
    dims = list(preds.keys())
    active = {d: [(lab, m) for lab, m in preds[d] if m is not None] for d in dims}
    out = []
    for k in range(0, max_active + 1):
        for chosen_dims in itertools.combinations(dims, k):
            for opts in itertools.product(*[active[d] for d in chosen_dims]):
                labels = {d: "不管" for d in dims}
                masks = []
                for d, (lab, m) in zip(chosen_dims, opts):
                    labels[d] = lab
                    masks.append(m)
                out.append((labels, masks))
    return out


def scan(panel, preds, cost=config.COST, min_n=100, fwd_override=None,
         max_active=4, combos=None):
    """
    掃組合 × 所有 hold。回 list[dict],按 score 由大到小。
    fwd_override:{h: 打亂後的前向報酬} — 置換檢定用。
    combos:預先產生的組合(置換時重用,省時)。
    """
    out = []
    for labels, masks in (combos or iter_combos(preds, max_active)):
        if masks:
            mask = np.logical_and.reduce(masks)
            if mask.sum() < min_n:
                continue
        else:
            mask = None
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
                   min_n=100, verbose=True, max_active=4, combos=None):
    """
    跑 trials 次「打亂後的完整搜尋」,收集每次的最佳 score。
    回 (p_value, null_bests)。p = 亂掃最佳 >= 真實最佳 的比例。
    """
    combos = combos or iter_combos(preds, max_active)
    nulls = []
    for t in range(trials):
        fwd = permute_fwd(panel)
        res = scan(panel, preds, cost=cost, min_n=min_n, fwd_override=fwd, combos=combos)
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

    # 基準線:同一批日期下「全體股票」的股票層面中位數。
    # 個股報酬右偏(少數飆股拉高平均),而基準是平均 → 任何一群股票的中位數都會是負的。
    # 所以中位數必須**相對比較**,不能用「<0 就死」當標準(那會把所有訊號都誤殺)。
    dmask = np.isin(panel["dates"], np.array(uniq_d))
    umask = dmask & ~np.isnan(ex)
    us, uv = panel["sid"][umask], ex[umask]
    uniq_us = np.unique(us)
    uni_svals = np.array([uv[us == s].mean() for s in uniq_us]) if len(uniq_us) else np.array([0.0])
    base_stock_median = float(np.median(uni_svals))
    base_stock_pos = float((uni_svals > 0).mean() * 100)

    order = np.argsort(-dvals)
    keep = np.ones(len(dvals), bool)
    keep[order[:3]] = False
    drop3 = float(np.average(dvals[keep], weights=dw[keep])) if keep.sum() else float("nan")

    so = np.argsort(-scontrib)
    # 分母用「所有正貢獻總和」= 總獲利。用淨額當分母會被負貢獻抵銷成 >100%,讀不懂。
    tot = scontrib[scontrib > 0].sum()
    return {
        "n_rows": len(idx), "n_stocks": len(uniq_s), "n_dates": len(uniq_d),
        "mean": float(vv.mean()), "norm20": float(vv.mean() * 20.0 / h),
        "date_pos": float((dvals > 0).mean() * 100), "date_median": float(np.median(dvals)),
        "stock_pos": float((svals > 0).mean() * 100), "stock_median": float(np.median(svals)),
        "base_stock_median": base_stock_median, "base_stock_pos": base_stock_pos,
        "rel_stock_median": float(np.median(svals)) - base_stock_median,
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
    print(f"{indent}股票層面:正 {c['stock_pos']:.0f}%  中位 {c['stock_median']*100:+.2f}%"
          f"   (同期全體基準 正 {c['base_stock_pos']:.0f}% 中位 {c['base_stock_median']*100:+.2f}%"
          f" → 相對 {c['rel_stock_median']*100:+.2f}%)")
    print(f"{indent}貢獻集中:前5檔佔總獲利 {c['top5_share']:.0f}%、前10檔佔 {c['top10_share']:.0f}%")
    print(f"{indent}  最大貢獻:{', '.join(s for s, _ in c['top_contrib'][:6])}")

    bad = []
    # 注意:個股報酬右偏 → 對「平均」基準,任何一群股票中位數都會是負的。
    # 因此比的是「相對同期全體」的中位數,不是絕對值。
    if c["rel_stock_median"] <= 0:
        bad.append("個股中位數贏不過同期全體(相對≤0)")
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
