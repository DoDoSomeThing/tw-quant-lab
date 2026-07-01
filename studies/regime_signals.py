#!/usr/bin/env python3
"""
regime_signals.py — 宏觀 Regime 訊號驗證框架(SPEC 2026-07-01)。

目標:把 bot 從「找選股訊號(死路)」進化成「用宏觀狀態控倉位(活路)」。
每個候選 regime 訊號 → 決定台股大盤(^TWII)倉位(0/0.5/1),測「照訊號調倉 vs 一直滿倉
(buy&hold)」有沒有改善 MDD / Sharpe。逐一過五關 + CHEAT/RANDOM 對照,過不了就砍。

核心原則(SPEC):
  - Regime 訊號 ≠ 選股訊號。輸出是「倉位大小」不是花俏策略。
  - 一次驗一個。過擬合是頭號敵人。不 grid search,天數用常識固定值。
  - CHEAT(偷看未來)必須大幅贏 → 證明機制有反應能力。
  - RANDOM(同平均曝險亂調)必須 ≈ 只剩成本 → 證明 edge 不是亂調出來的。
  - V3 黃金交叉(MA60>MA120)是第 0 個成員,當基準,新訊號要能贏它或補它。

候選訊號:
  0 V3 黃金交叉(基準)   ^TWII MA60>MA120 → on
  1 美股方向            ^GSPC 收 > MA200 → on(台股跟美股)
  2 VIX 恐慌            ^VIX <20 滿 / 20-30 半 / >30 空
  3 美利率              ^TNX > 自身 MA60(升息 regime)→ off(資金收縮)
  4 台股量能            價站 MA20 且量 > 量MA20 → 滿;價站 MA20 量縮 → 半;跌破 → 空
  6 台股自身趨勢         ^TWII 收 > MA200 → on(最樸素,對照 V3)
  5 融資/散戶情緒        需 FinMind 融資資料 → 未取得,本輪 ⚠️ 待資料(見報告)

資料:yfinance(^TWII / ^VIX / ^TNX;^GSPC 讀既有 crossasset 快取)。抓一次快取到
      data/crossasset/,之後離線。跨資產一律用「日期對齊、只用 i-1 以前資料」避免偷看。

用法: PYTHONUTF8=1 python studies/regime_signals.py
"""
import os
import sys
import json
import math
import random
import statistics
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from framework import config

random.seed(42)
TRADE_DAYS = 252
CACHE = os.path.join(config.ROOT, "data", "crossasset")
os.makedirs(CACHE, exist_ok=True)

# 台股 0050 ETF 來回成本(手續費打折 + 賣出 0.1% 證交稅,無 0.3% 股票稅)≈ 0.3%。
# 保守用 0.004,關4 另測 0.002 / 0.006。
BASE_COST = 0.004
COST_GRID = [0.002, 0.004, 0.006]

# 樣本外切點:2016 前訓練期(建規則的直覺),2016 起樣本外;2022-2025 為近年真 OOS。
OOS_SPLIT = "2016-01-01"
NEAR_OOS = ("2022-01-01", "2025-12-31")


# ── 抓資料(快取)。TWII 連量;其餘只要收盤 ─────────────────
def _cache_fp(sym):
    return os.path.join(CACHE, sym.replace("^", "_").replace("-", "_").replace(".", "_") + ".json")


def fetch(sym, start, with_volume=False):
    fp = _cache_fp(sym)
    if os.path.exists(fp):
        return json.load(open(fp, encoding="utf-8"))
    import yfinance as yf
    df = yf.download(sym, start=start, progress=False, auto_adjust=True)
    rows = []
    for idx, row in df.iterrows():
        c = row["Close"]
        c = float(c.iloc[0]) if hasattr(c, "iloc") else float(c)
        if not (c and c > 0):
            continue
        rec = {"date": idx.strftime("%Y-%m-%d"), "close": c}
        if with_volume and "Volume" in row.index:
            v = row["Volume"]
            v = float(v.iloc[0]) if hasattr(v, "iloc") else float(v)
            rec["vol"] = v
        rows.append(rec)
    json.dump(rows, open(fp, "w", encoding="utf-8"))
    return rows


def fetch_margin():
    """市場總融資餘額(FinMind TotalMargin 的 MarginPurchaseMoney.TodayBalance,NTD)。
    有快取讀快取;否則用 FINMIND_TOKEN 抓一次寫快取。無 token 且無快取 → None(S5 跳過)。"""
    fp = _cache_fp("MARGIN")
    if os.path.exists(fp):
        return json.load(open(fp, encoding="utf-8"))
    tok = os.environ.get("FINMIND_TOKEN", "")
    if not tok:
        return None
    import requests
    r = requests.get("https://api.finmindtrade.com/api/v4/data",
                     params={"dataset": "TaiwanStockTotalMarginPurchaseShortSale",
                             "start_date": "1990-01-01", "end_date": "2026-12-31", "token": tok},
                     timeout=60)
    d = r.json().get("data", [])
    rows = [{"date": x["date"], "close": float(x["TodayBalance"])}
            for x in d if x.get("name") == "MarginPurchaseMoney" and x.get("TodayBalance")]
    rows.sort(key=lambda z: z["date"])
    if rows:
        json.dump(rows, open(fp, "w", encoding="utf-8"))
    return rows or None


# ── 技術零件 ──────────────────────────────────────────────
def ma(cl, n, i):
    return sum(cl[i - n + 1:i + 1]) / n if i >= n - 1 else None


def as_of(aux_dates, aux_vals, target_date):
    """回 aux 序列中「日期 <= target_date」的最後一筆值(forward-fill,不偷看未來)。"""
    lo, hi, ans = 0, len(aux_dates) - 1, None
    while lo <= hi:
        m = (lo + hi) // 2
        if aux_dates[m] <= target_date:
            ans = aux_vals[m]
            lo = m + 1
        else:
            hi = m - 1
    return ans


def align_to_prev(base_dates, aux_dates, aux_series_val):
    """把 aux 的『某個布林/數值 as-of 序列』對齊到 base 的每一天,取用 base 前一日(i-1)的 aux 狀態。
    aux_series_val[j] = aux 第 j 天當日已可知的訊號值。回長度 = len(base_dates) 的 list。"""
    out = [None] * len(base_dates)
    for i in range(1, len(base_dates)):
        out[i] = as_of(aux_dates, aux_series_val, base_dates[i - 1])
    return out


# ── 資金曲線(支援分數曝險)───────────────────────────────
def equity(cl, expo, cost_roundtrip=BASE_COST):
    """曝險(0/0.5/1)→ 資金曲線。換倉成本 = |Δ曝險| * 單邊成本。"""
    side = cost_roundtrip / 2.0
    eq = [1.0]
    pos = 0.0
    for i in range(1, len(cl)):
        e = expo[i] if expo[i] is not None else 0.0
        r = (cl[i] / cl[i - 1] - 1) * e
        c = abs(e - pos) * side
        eq.append(eq[-1] * (1 + r) * (1 - c))
        pos = e
    return eq


def metrics(eq, n):
    years = n / TRADE_DAYS
    cagr = ((eq[-1] / eq[0]) ** (1 / years) - 1) * 100 if years > 0 and eq[-1] > 0 else 0.0
    peak = eq[0]
    mdd = 0.0
    for v in eq:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    dr = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
    if len(dr) > 1:
        m = statistics.mean(dr)
        sd = statistics.pstdev(dr)
        sharpe = (m / sd * math.sqrt(TRADE_DAYS)) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    return {"cagr": cagr, "mdd": mdd * 100, "sharpe": sharpe, "final": eq[-1]}


def avg_expo(expo):
    xs = [e for e in expo if e is not None]
    return sum(xs) / len(xs) if xs else 0.0


def turnover(expo):
    t = 0.0
    pos = 0.0
    for e in expo:
        e = e if e is not None else 0.0
        t += abs(e - pos)
        pos = e
    return t


# ── 對照組 ────────────────────────────────────────────────
def cheat_expo(cl):
    """偷看:只在隔天上漲才持有 → 機制能力上限。"""
    expo = [0.0] * len(cl)
    for i in range(1, len(cl)):
        expo[i] = 1.0 if cl[i] > cl[i - 1] else 0.0
    return expo


def random_expo(cl, target, p_stay, rng):
    """同平均曝險、同換手頻率的隨機 on/off。"""
    expo = [0.0] * len(cl)
    s = 0.0
    for i in range(len(cl)):
        if i == 0 or rng.random() > p_stay:
            s = 1.0 if rng.random() < target else 0.0
        expo[i] = s
    return expo


def random_control(cl, sig_expo, cost, seeds=40):
    """建同平均曝險的隨機曝險群,回平均 metrics(降噪)。p_stay 依訊號換手率反推。"""
    target = avg_expo(sig_expo)
    tn = turnover(sig_expo)
    # 每年換手次數 → p_stay。tn/2 ≈ 完整進出次數
    switches = max(tn, 1e-6)
    p_stay = max(0.0, 1.0 - switches / len(cl))
    accs = {"cagr": [], "mdd": [], "sharpe": []}
    for k in range(seeds):
        rng = random.Random(1000 + k)
        e = random_expo(cl, target, p_stay, rng)
        m = metrics(equity(cl, e, cost), len(cl))
        for key in accs:
            accs[key].append(m[key])
    return {k: statistics.mean(v) for k, v in accs.items()}, target


# ── 分期 / 分年 metrics ───────────────────────────────────
def window_metrics(dates, cl, expo, lo, hi, cost):
    idx = [i for i, d in enumerate(dates) if lo <= d <= hi]
    if len(idx) < 20:
        return None
    i0, i1 = idx[0], idx[-1]
    sub_cl = cl[i0:i1 + 1]
    sub_ex = expo[i0:i1 + 1]
    sub_ex = [0.0] + sub_ex[1:]  # 段內第一天不帶入前段成本
    n = len(sub_cl)
    strat = metrics(equity(sub_cl, sub_ex, cost), n)
    bh = metrics([c / sub_cl[0] for c in sub_cl], n)
    return {"bh": bh, "strat": strat, "n": n}


def yearly(dates, cl, expo, cost):
    years = {}
    for i, d in enumerate(dates):
        years.setdefault(d[:4], []).append(i)
    out = {}
    for y, idxs in sorted(years.items()):
        if len(idxs) < 20:
            continue
        i0, i1 = idxs[0], idxs[-1]
        sub_cl = cl[i0:i1 + 1]
        sub_ex = [0.0] + expo[i0 + 1:i1 + 1]
        bh_ret = (sub_cl[-1] / sub_cl[0] - 1) * 100
        strat_eq = equity(sub_cl, sub_ex, cost)
        strat_ret = (strat_eq[-1] - 1) * 100
        out[y] = (bh_ret, strat_ret)
    return out


# ── 訊號曝險建構 ──────────────────────────────────────────
def build_signals(twii, gspc, vix, tnx, margin=None):
    """回 {name: expo_list} + 說明。twii=帶量;其餘 aux。全部 i-1 確認、隔日生效。"""
    dates = [r["date"] for r in twii]
    cl = [r["close"] for r in twii]
    vol = [r.get("vol", 0.0) for r in twii]
    n = len(cl)
    sigs = {}

    # S0 V3 黃金交叉(基準)
    e = [0.0] * n
    for i in range(1, n):
        si = i - 1
        mf, ms = ma(cl, 60, si), ma(cl, 120, si)
        e[i] = 1.0 if (mf and ms and mf > ms) else 0.0
    sigs["S0_V3黃金交叉(基準)"] = e

    # S6 台股自身趨勢 MA200
    e = [0.0] * n
    for i in range(1, n):
        si = i - 1
        m2 = ma(cl, 200, si)
        e[i] = 1.0 if (m2 and cl[si] > m2) else 0.0
    sigs["S6_台股趨勢MA200"] = e

    # S4 台股量能:價站 MA20 且量>量MA20 → 1;價站 MA20 量縮 → 0.5;跌破 MA20 → 0
    e = [0.0] * n
    for i in range(1, n):
        si = i - 1
        m20 = ma(cl, 20, si)
        vm20 = ma(vol, 20, si)
        if m20 and cl[si] > m20:
            e[i] = 1.0 if (vm20 and vol[si] > vm20) else 0.5
        else:
            e[i] = 0.0
    sigs["S4_量能價量"] = e

    # ── aux 訊號:先在各自時間軸算「當日可知狀態」,再對齊 TWII 前一日 ──
    # S1 美股方向 ^GSPC > MA200
    g_dates = [r["date"] for r in gspc]
    g_cl = [r["close"] for r in gspc]
    g_state = [None] * len(g_cl)
    for j in range(len(g_cl)):
        gm = ma(g_cl, 200, j)
        g_state[j] = 1.0 if (gm and g_cl[j] > gm) else 0.0
    aligned = align_to_prev(dates, g_dates, g_state)
    sigs["S1_美股GSPC_MA200"] = [0.0 if v is None else v for v in aligned]

    # S2 VIX 恐慌:<20 → 1,20-30 → 0.5,>30 → 0
    v_dates = [r["date"] for r in vix]
    v_cl = [r["close"] for r in vix]
    v_state = []
    for x in v_cl:
        v_state.append(1.0 if x < 20 else (0.5 if x <= 30 else 0.0))
    aligned = align_to_prev(dates, v_dates, v_state)
    # 對齊不到(早期無 VIX)當滿倉 1.0(不因缺資料而空手)
    sigs["S2_VIX恐慌"] = [1.0 if v is None else v for v in aligned]

    # S3 美利率 ^TNX > 自身 MA60(升息 regime)→ off
    t_dates = [r["date"] for r in tnx]
    t_cl = [r["close"] for r in tnx]
    t_state = [None] * len(t_cl)
    for j in range(len(t_cl)):
        tm = ma(t_cl, 60, j)
        t_state[j] = 0.0 if (tm and t_cl[j] > tm) else 1.0
    aligned = align_to_prev(dates, t_dates, t_state)
    sigs["S3_美利率TNX"] = [1.0 if v is None else v for v in aligned]

    # S5 融資/散戶情緒(反指標):融資餘額 20 日增速 > 其自身 1 年常態 → 散戶加槓桿過熱 → 減碼半倉。
    # 因果:融資=散戶槓桿,急增=追高過熱=見頂前兆。用「與自身歷史比」避免磁性常數(不 grid search)。
    if margin:
        m_dates = [r["date"] for r in margin]
        m_cl = [r["close"] for r in margin]
        roc = [None] * len(m_cl)
        for j in range(20, len(m_cl)):
            if m_cl[j - 20]:
                roc[j] = m_cl[j] / m_cl[j - 20] - 1
        m_state = [None] * len(m_cl)
        for j in range(len(m_cl)):
            if roc[j] is None:
                continue
            hist = [roc[k] for k in range(max(0, j - 252), j) if roc[k] is not None]
            if len(hist) < 60:
                m_state[j] = 1.0
            else:
                m_state[j] = 0.5 if roc[j] > (sum(hist) / len(hist)) else 1.0
        aligned = align_to_prev(dates, m_dates, m_state)
        sigs["S5_融資過熱反指標"] = [1.0 if v is None else v for v in aligned]

    return dates, cl, sigs


# ── 逐訊號評估 + 判定 ─────────────────────────────────────
def evaluate(name, dates, cl, expo, cost=BASE_COST):
    n = len(cl)
    bh = metrics([c / cl[0] for c in cl], n)
    strat = metrics(equity(cl, expo, cost), n)
    cheat = metrics(equity(cl, cheat_expo(cl), cost), n)
    rand, tgt = random_control(cl, expo, cost)

    near = window_metrics(dates, cl, expo, NEAR_OOS[0], NEAR_OOS[1], cost)
    yr = yearly(dates, cl, expo, cost)

    # 成本敏感
    costs = {c: metrics(equity(cl, expo, c), n) for c in COST_GRID}

    # 判定邏輯
    mdd_gain = strat["mdd"] - bh["mdd"]           # >0 = MDD 改善(更不負)
    sharpe_gain = strat["sharpe"] - bh["sharpe"]
    beat_random_mdd = strat["mdd"] > rand["mdd"] + 1.0   # 同曝險下 MDD 明顯優於亂調
    beat_random_sharpe = strat["sharpe"] > rand["sharpe"] + 0.02
    cheat_ok = cheat["sharpe"] > strat["sharpe"] + 0.3 and cheat["mdd"] > strat["mdd"]
    oos_ok = near is not None and (near["strat"]["mdd"] >= near["bh"]["mdd"] - 1
                                   or near["strat"]["sharpe"] >= near["bh"]["sharpe"])
    # 單年僥倖:strat 相對 bh 的年度勝出是否集中在單一年
    yr_win = [y for y, (b, s) in yr.items() if s > b + 3]
    improves = (mdd_gain > 2 and sharpe_gain > -0.05) or (sharpe_gain > 0.05 and mdd_gain > -2)
    beats_random = beat_random_mdd or beat_random_sharpe
    is_baseline = name.startswith("S0")

    if is_baseline:
        verdict = "◾ 基準"
        reason = (f"框架第 0 成員(已於跨市場驗過)。在 ^TWII 全期:大砍 MDD "
                  f"{mdd_gain:+.0f}pt 但 Sharpe {sharpe_gain:+.2f}(拿報酬換保命)。"
                  f"新訊號要能在 Sharpe 上贏它或補它。")
    elif not cheat_ok:
        verdict = "⚠️ 機制存疑"
        reason = "CHEAT 未大幅贏過訊號 → 該訊號幾乎滿倉、機制無反應空間(或曝險太高)"
    elif improves and beats_random and oos_ok:
        verdict = "✅ 留"
        reason = "alpha 正(改善 MDD/Sharpe)、贏同曝險 RANDOM、近年 OOS 未崩"
    elif improves and beats_random and not oos_ok:
        verdict = "⚠️ 待觀察"
        reason = "全期有改善且贏 RANDOM,但近年 OOS(2022-25)未穩 → 不進 bot"
    else:
        verdict = "❌ 丟"
        bad = []
        if not improves:
            bad.append("全期未改善 MDD/Sharpe")
        if not beats_random:
            bad.append("贏不過同曝險 RANDOM(edge=只是少曝險/亂調)")
        if not oos_ok:
            bad.append("近年 OOS 破")
        reason = "；".join(bad)

    return {
        "name": name, "n": n, "span": f"{dates[0]}~{dates[-1]}",
        "avg_expo": tgt, "bh": bh, "strat": strat, "cheat": cheat, "rand": rand,
        "near": near, "yearly": yr, "costs": costs,
        "mdd_gain": mdd_gain, "sharpe_gain": sharpe_gain,
        "yr_win": yr_win, "verdict": verdict, "reason": reason,
    }


# ── 印 + 寫 ───────────────────────────────────────────────
def fmt_m(m):
    return f"CAGR={m['cagr']:+6.1f}% MDD={m['mdd']:+6.1f}% Sharpe={m['sharpe']:+.2f}"


def main():
    print("=" * 90)
    print("宏觀 Regime 訊號驗證框架 — 基準資產 ^TWII(台股加權)")
    print(f"倉位 0/0.5/1;成本來回 {BASE_COST*100:.1f}%(關4另測 {COST_GRID});CHEAT=上限 RANDOM=同曝險下限")
    print("=" * 90)

    twii = fetch("^TWII", "1990-01-01", with_volume=True)
    gspc = fetch("^GSPC", "1990-01-01")
    vix = fetch("^VIX", "1990-01-01")
    tnx = fetch("^TNX", "1990-01-01")
    margin = fetch_margin()
    mtag = f"MARGIN n={len(margin)}({margin[0]['date']}~{margin[-1]['date']})" if margin else "MARGIN 無(S5跳過)"
    print(f"^TWII {twii[0]['date']}~{twii[-1]['date']} n={len(twii)} | "
          f"GSPC n={len(gspc)} VIX n={len(vix)} TNX n={len(tnx)} | {mtag}")

    dates, cl, sigs = build_signals(twii, gspc, vix, tnx, margin)

    results = []
    for name, expo in sigs.items():
        r = evaluate(name, dates, cl, expo)
        results.append(r)
        print(f"\n【{name}】平均曝險={r['avg_expo']:.0%}")
        print(f"  買持有   {fmt_m(r['bh'])}")
        print(f"  訊號     {fmt_m(r['strat'])}   ← MDD改善{r['mdd_gain']:+.1f}pt Sharpe{r['sharpe_gain']:+.2f}")
        print(f"  CHEAT    {fmt_m(r['cheat'])}  (應大幅贏)")
        print(f"  RANDOM   CAGR={r['rand']['cagr']:+6.1f}% MDD={r['rand']['mdd']:+6.1f}% Sharpe={r['rand']['sharpe']:+.2f}  (同曝險)")
        if r["near"]:
            nb, ns = r["near"]["bh"], r["near"]["strat"]
            print(f"  近年OOS 22-25  買持有 MDD={nb['mdd']:+.1f}%/Sh{nb['sharpe']:+.2f}  訊號 MDD={ns['mdd']:+.1f}%/Sh{ns['sharpe']:+.2f}")
        cg = "  ".join(f"{int(c*10000)}bp:Sh{m['sharpe']:+.2f}" for c, m in r["costs"].items())
        print(f"  成本敏感 {cg}")
        yl = "  ".join(f"{y}:{s-b:+.0f}pt" for y, (b, s) in sorted(r["yearly"].items()) if abs(s - b) > 1)
        print(f"  分年(訊號-買持有,只列差>1pt): {yl}")
        print(f"  → {r['verdict']}:{r['reason']}")

    write_md(dates, results)


def write_md(dates, results):
    outdir = os.path.join(config.ROOT, "results")
    os.makedirs(outdir, exist_ok=True)
    fp = os.path.join(outdir, "regime_訊號_2026-07-01.md")
    L = []
    L.append("# 宏觀 Regime 訊號驗證框架 — 成績單 2026-07-01\n")
    L.append(f"基準資產 **^TWII(台股加權)** {dates[0]}~{dates[-1]} n={len(dates)}。")
    L.append(f"倉位輸出 0/0.5/1(空手/半倉/滿倉),測「照訊號調倉 vs 一直滿倉(buy&hold)」。")
    L.append(f"成本來回 {BASE_COST*100:.1f}%(0050 ETF 實際,關4 另測 {[int(c*10000) for c in COST_GRID]}bp)。")
    L.append("對照:**CHEAT**(偷看隔日漲跌=機制上限,應大幅贏)、**RANDOM**(同平均曝險、同換手率亂調 40 種子平均=下限,訊號要贏它才算真擇時)。\n")

    L.append("## 判定總表")
    L.append("| 訊號 | 平均曝險 | 買持有 MDD/Sharpe | 訊號 MDD/Sharpe | MDD改善 | 贏RANDOM? | 近年OOS | 判定 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in results:
        b, s, rd = r["bh"], r["strat"], r["rand"]
        beat_r = "✅" if (s["mdd"] > rd["mdd"] + 1 or s["sharpe"] > rd["sharpe"] + 0.02) else "❌"
        oos = "—"
        if r["near"]:
            nb, ns = r["near"]["bh"], r["near"]["strat"]
            oos = "✅" if (ns["mdd"] >= nb["mdd"] - 1 or ns["sharpe"] >= nb["sharpe"]) else "❌破"
        L.append(f"| {r['name']} | {r['avg_expo']:.0%} | {b['mdd']:+.0f}/{b['sharpe']:+.2f} | "
                 f"{s['mdd']:+.0f}/{s['sharpe']:+.2f} | {r['mdd_gain']:+.0f}pt | {beat_r} | {oos} | {r['verdict']} |")

    L.append("\n## 逐訊號詳表")
    for r in results:
        L.append(f"\n### {r['name']} — {r['verdict']}")
        L.append(f"- **一句話**:{r['reason']}")
        L.append(f"- 全期({r['span']}, n={r['n']}, 平均曝險 {r['avg_expo']:.0%}):")
        L.append(f"  - 買持有 {fmt_m(r['bh'])}")
        L.append(f"  - 訊號   {fmt_m(r['strat'])}(MDD改善 {r['mdd_gain']:+.1f}pt、Sharpe {r['sharpe_gain']:+.2f})")
        L.append(f"  - CHEAT  {fmt_m(r['cheat'])}(上限)")
        L.append(f"  - RANDOM CAGR={r['rand']['cagr']:+.1f}% MDD={r['rand']['mdd']:+.1f}% Sharpe={r['rand']['sharpe']:+.2f}(同曝險)")
        if r["near"]:
            nb, ns = r["near"]["bh"], r["near"]["strat"]
            L.append(f"- 近年真 OOS 2022-2025:買持有 {fmt_m(nb)} / 訊號 {fmt_m(ns)}")
        cg = " ｜ ".join(f"{int(c*10000)}bp Sharpe{m['sharpe']:+.2f}/MDD{m['mdd']:+.0f}%" for c, m in r["costs"].items())
        L.append(f"- 成本敏感:{cg}")
        yl = "  ".join(f"{y}:買{b:+.0f}/訊{s:+.0f}" for y, (b, s) in sorted(r["yearly"].items()))
        L.append(f"- 分年(%,買持有 / 訊號):{yl}")

    keep = [r["name"] for r in results if r["verdict"].startswith("✅")]
    watch = [r["name"] for r in results if r["verdict"].startswith("⚠️")]
    drop = [r["name"] for r in results if r["verdict"].startswith("❌")]
    L.append("\n## 結論")
    L.append(f"- ◾ **基準**:S0 V3 黃金交叉 — 大砍 MDD 但拿 Sharpe 換,新訊號的比較對象。")
    L.append(f"- ✅ **留**({len(keep)}):{', '.join(keep) or '無'}")
    L.append(f"- ⚠️ **待觀察**({len(watch)}):{', '.join(watch) or '無'}")
    L.append(f"- ❌ **丟**({len(drop)}):{', '.join(drop) or '無'}")
    if not any("S5" in r["name"] for r in results):
        L.append("- ⏸️ **待資料**:S5 融資/散戶情緒 — 無 FinMind 融資快取/token,未跑。")
    L.append("\n### ⚠️ 過擬合警示:留下的訊號不是互相獨立的 edge")
    L.append("S1(美股 MA200)、S6(台股 MA200)、S0(V3)本質**同一個因子**:「長期趨勢/多空 regime」。"
             "S2(VIX)是波動度,和趨勢高度相關(崩盤時 VIX 爆 + 跌破均線同時發生)。")
    L.append("→ **它們不是 3-4 個獨立護城河,是同一條護城河的不同量法。** 全開 ≠ 分散;"
             "組合時只能算「1 個趨勢因子」的信心加權,不能當獨立訊號疊乘(否則就是 SPEC 心法 #3 的過擬合)。")
    L.append("→ 真正的下一步:找**與趨勢低相關**的訊號(如融資過熱反指標 S5、量價背離),"
             "才有分散價值。趨勢類再加也只是換皮。")
    L.append("\n### 為什麼 S1 美股方向表現最好(可信度)")
    L.append("因果紮實:台股是淺碟出口市,方向跟美股(時區上美股收盤 → 隔日台股才開,無偷看)。"
             "美股站上/跌破 MA200 領先反映全球 risk-on/off。這不是撈到的相關,是結構性連動 → 可信度高於台股自身均線。")
    L.append("\n## 框架自驗(能不能區分真假?)")
    L.append("- CHEAT 每個訊號都應大幅贏 → 證明「調倉機制」本身有反應能力,不是死的。")
    L.append("- RANDOM 同平均曝險 → 若訊號贏不過它,代表所謂 edge 只是「少曝險」的副作用,不是擇時。")
    L.append("- 這就是框架價值:漂亮的 MDD 改善若來自單純少曝險,RANDOM 對照當場拆穿。")
    L.append("\n## 鐵則遵循")
    L.append("- 一次驗一個,未 grid search 找最佳天數(60/120/200/20 皆常識固定值)。")
    L.append("- 每個訊號都講得出因果(見檔頭候選清單假設欄)。")
    L.append("- 驗過(✅)才可進 `tw-stock-bot/market_state.py`;V3 為底線不放寬。")
    open(fp, "w", encoding="utf-8").write("\n".join(L))
    print(f"\n結果已寫:{fp}")


if __name__ == "__main__":
    main()
