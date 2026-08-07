#!/usr/bin/env python3
"""
資料新鮮度檢測 + 增量更新。對著 config 的資料家(預設 env QLAB_DATA_DIR)。

  python backfill/update_data.py --check     只檢測,印新不新鮮,不抓
  python backfill/update_data.py             檢測,過期才抓(增量補到今天)
  python backfill/update_data.py --force      不管新舊,直接增量更新

新鮮度規則:
  kline_deep.json : 最後一根日K < 最近一個交易日(給 1 天寬限,今天的K當晚才出) → 過期
  revenue.json    : 最新營收月份 < 應已公布的月份(每月約10號公布,過12號才算該有上月) → 過期
增量:只補「現有股票」缺的部分(kline 從最後日期接到今天;revenue 補新月份)。
新上市股票不在此處理,需要時另跑 backfill_kline.py / backfill_revenue.py 全量。
撞 FinMind 配額(402)自動睡到整點重置續跑。需 FINMIND_TOKEN(--check 不需要)。
"""
import os
import sys
import json
import time
import argparse
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from framework import config
from framework.finmind import http_get, get_logger, FINMIND_TOKEN, FINMIND_API

logger = get_logger("update_data")
KLINE_GRACE_DAYS = 1
SAVE_EVERY, SLEEP_OK, HOUR_BUFFER = 50, 0.4, 120

# ---- TWSE 全市場逐日(kline 預設來源,免 token 免配額)----
# 2026-08-07 改:原本 kline 走 FinMind 逐檔,GitHub Actions 上連不到 FinMind 時
# 每檔要 3×40s timeout + backoff ≈ 138 秒,1087 檔 ≈ 42 小時,而且 st=="fail"
# 只是 i+=1 繼續 → 無聲磨到 job timeout(實測 run 31139400990 跑 3.4 小時、
# FinMind 配額用量 0)。TWSE rwd 帶 date 一個請求拿全市場一天,補一個月只要 ~20 個請求。
TWSE_MI_INDEX = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TWSE_SLEEP = 3.0          # TWSE 對連打敏感,節流
TWSE_MAX_FAILS = 5        # 連續失敗上限 → 中止並報錯,不再無聲繼續
TWSE_MAX_DAYS = 400       # 缺口超過此天數 → 要求改跑全量 backfill,不硬撐


# ---------- 日期工具 ----------
def last_weekday(d):
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def prev_month_ym(d):
    """d 的上一個月 → yyyymm。"""
    y, m = d.year, d.month - 1
    if m == 0:
        y, m = y - 1, 12
    return y * 100 + m


def _secs_to_next_hour():
    now = datetime.now()
    nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return int((nxt - now).total_seconds()) + HOUR_BUFFER


# ---------- 新鮮度檢測 ----------
KLINE_STALE_FRAC = 0.10   # 落後股票超過此比例 → 判過期(容許少數已下市/停牌的永久舊資料)


def kline_status(deep):
    """
    回 (stale, max_date, target_date, behind, total)。
    不只看最大日期(會被單檔矇騙),改看「落後 target 的股票比例」。
    """
    target = last_weekday(date.today() - timedelta(days=KLINE_GRACE_DAYS)).isoformat()
    lasts = [bars[-1]["date"] for bars in deep.values() if bars]
    total = len(lasts)
    mx = max(lasts) if lasts else "(空)"
    behind = sum(1 for d in lasts if d < target)
    stale = (behind / total > KLINE_STALE_FRAC) if total else True
    return stale, mx, target, behind, total


def revenue_status(rev):
    """回 (stale, latest_ym, expected_ym)。"""
    latest = 0
    for rows in rev.values():
        for _, ym, _r in rows:
            if ym > latest:
                latest = ym
    today = date.today()
    # 每月約10號公布上月;過12號才算「上月該有了」,否則期望到上上月
    ref = today if today.day >= 12 else today.replace(day=1) - timedelta(days=1)
    expected = prev_month_ym(ref if today.day >= 12 else ref.replace(day=15))
    return (latest < expected if latest else True), latest or 0, expected


# ---------- 抓取 ----------
def fetch_kline(sid, start, end):
    try:
        j = http_get(FINMIND_API, params={"dataset": config.PRICE_DATASET, "data_id": sid,
                     "start_date": start, "end_date": end, "token": FINMIND_TOKEN}, timeout=40).json()
    except Exception:
        return [], "fail"
    st, msg = j.get("status"), str(j.get("msg", ""))
    if st != 200:
        return [], "quota" if (st == 402 or "upper limit" in msg.lower()) else "fail"
    return [{"date": x["date"], "open": float(x["open"]), "max": float(x["max"]),
             "min": float(x["min"]), "close": float(x["close"]),
             "volume": float(x["Trading_Volume"])} for x in j.get("data", [])], "ok"


def fetch_revenue(sid, start, end):
    try:
        j = http_get(FINMIND_API, params={"dataset": "TaiwanStockMonthRevenue", "data_id": sid,
                     "start_date": start, "end_date": end, "token": FINMIND_TOKEN}, timeout=40).json()
    except Exception:
        return [], "fail"
    st = j.get("status")
    if st != 200:
        return [], "quota" if (st == 402 or "upper limit" in str(j.get("msg", "")).lower()) else "fail"
    rows = []
    for x in j.get("data", []):
        ym = x["revenue_year"] * 100 + x["revenue_month"]
        avail = (datetime.strptime(x["date"], "%Y-%m-%d") + timedelta(days=11)).strftime("%Y-%m-%d")
        rows.append([avail, ym, float(x["revenue"])])
    return rows, "ok"


def _twse_num(s):
    """'2,390.00' → 2390.0;'--' / '' / None → None。"""
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "-", "--", "---"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_market_day(ymd):
    """
    TWSE 全市場某一天的 OHLCV。回 {sid: bar};非交易日回 {}。
    網路失敗 raise(由呼叫端計數中止),不吞掉 —— 吞掉就會變成無聲磨。
    """
    r = http_get(TWSE_MI_INDEX, timeout=30,
                 params={"date": ymd, "type": "ALLBUT0999", "response": "json"},
                 headers={"User-Agent": "tw-quant-lab/1.0"})
    j = r.json()
    if j.get("stat") != "OK":
        return {}                                  # 非交易日 / 無資料
    tbl = None
    for t in j.get("tables", []):
        if "證券代號" in (t.get("fields") or []) and len(t.get("data") or []) > 100:
            tbl = t
            break
    if tbl is None:
        return {}
    d = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
    out = {}
    for row in tbl["data"]:
        sid = str(row[0]).strip()
        vol = _twse_num(row[2])
        o, hi, lo, c = (_twse_num(row[5]), _twse_num(row[6]),
                        _twse_num(row[7]), _twse_num(row[8]))
        if None in (o, hi, lo, c) or c <= 0:
            continue                               # 當日無成交 → 跳過,不寫 close=0
        out[sid] = {"date": d, "open": o, "max": hi, "min": lo,
                    "close": c, "volume": vol or 0.0}
    return out


def update_kline_twse(deep, path):
    """
    走 TWSE 全市場逐日補 kline。只補「現有股票」缺的日子(與 FinMind 版語意相同),
    但一個請求拿一天全市場,不是一檔一請求。
    """
    lasts = [bars[-1]["date"] for bars in deep.values() if bars]
    if not lasts:
        logger.error("kline 是空的,請先跑 backfill_kline.py 全量。")
        return
    start = (datetime.strptime(min(lasts), "%Y-%m-%d") + timedelta(days=1)).date()
    end = date.today()
    days = (end - start).days + 1
    logger.info(f"[kline/TWSE] 缺口 {start} ~ {end}({days} 天),"
                f"最舊 {min(lasts)} / 最新 {max(lasts)}")
    if days > TWSE_MAX_DAYS:
        logger.error(f"缺口 {days} 天 > {TWSE_MAX_DAYS},別用增量硬補 —— "
                     f"改跑 backfill_kline.py 全量,或先下載 release 種子。")
        return
    if days <= 0:
        logger.info("[kline/TWSE] 沒有缺口。")
        return

    have = {c: {b["date"] for b in bars} for c, bars in deep.items()}
    fails = 0
    scanned = trading = new_bars = 0
    d = start
    while d <= end:
        if d.weekday() >= 5:                       # 週末直接跳,不浪費請求
            d += timedelta(days=1)
            continue
        try:
            rows = fetch_market_day(d.strftime("%Y%m%d"))
            fails = 0
        except Exception as e:
            fails += 1
            logger.warning(f"[kline/TWSE] {d} 抓取失敗({fails}/{TWSE_MAX_FAILS}):{e}")
            if fails >= TWSE_MAX_FAILS:
                _save(deep, path)
                raise SystemExit(
                    f"連續 {TWSE_MAX_FAILS} 天抓取失敗 → 中止。"
                    f"已寫入截至目前的資料到 {path}。請檢查對外連線。")
            time.sleep(TWSE_SLEEP * fails)
            continue                               # 同一天重試
        scanned += 1
        if rows:
            trading += 1
            for sid, bar in rows.items():
                bars = deep.get(sid)
                if bars is None:                   # 新上市:增量不處理(同 FinMind 版語意)
                    continue
                if bar["date"] in have[sid]:
                    continue
                bars.append(bar)
                have[sid].add(bar["date"])
                new_bars += 1
        d += timedelta(days=1)
        time.sleep(TWSE_SLEEP)
        if scanned % 20 == 0:
            _save(deep, path)
            logger.info(f"[kline/TWSE] 已掃 {scanned} 天(交易日 {trading}),"
                        f"新增 {new_bars} 根")

    for bars in deep.values():
        bars.sort(key=lambda b: b["date"])
    _save(deep, path)
    logger.info(f"[kline/TWSE] 完成:掃 {scanned} 天(交易日 {trading}),"
                f"新增 {new_bars} 根 → {path}")


def _save(obj, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ---------- 增量更新 ----------
def update_kline(deep, path):
    end = date.today().isoformat()
    codes = list(deep.keys())
    upd = new_bars = i = 0
    while i < len(codes):
        c = codes[i]
        bars = deep[c]
        last = bars[-1]["date"] if bars else "2021-06-01"
        start = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        if start > end:
            i += 1
            continue
        rows, st = fetch_kline(c, start, end)
        if st == "quota":
            _save(deep, path)
            w = _secs_to_next_hour()
            logger.warning(f"[kline] 配額爆 → 睡 {w//60} 分到整點續跑…")
            time.sleep(w)
            continue
        if st == "fail":
            i += 1
            time.sleep(2)
            continue
        if rows:
            have = {b["date"] for b in bars}
            add = [b for b in rows if b["date"] not in have]
            if add:
                bars.extend(add)
                bars.sort(key=lambda b: b["date"])
                upd += 1
                new_bars += len(add)
        i += 1
        time.sleep(SLEEP_OK)
        if i % SAVE_EVERY == 0:
            _save(deep, path)
            logger.info(f"[kline] {i}/{len(codes)} 檔已掃,更新 {upd} 檔 +{new_bars} 根")
    _save(deep, path)
    logger.info(f"[kline] 完成:更新 {upd} 檔,新增 {new_bars} 根 → {path}")


def update_revenue(rev, path):
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=150)).isoformat()   # 抓近5個月窗,補新月份
    codes = list(rev.keys())
    upd = new_rows = i = 0
    while i < len(codes):
        c = codes[i]
        rows, st = fetch_revenue(c, start, end)
        if st == "quota":
            _save(rev, path)
            w = _secs_to_next_hour()
            logger.warning(f"[revenue] 配額爆 → 睡 {w//60} 分到整點續跑…")
            time.sleep(w)
            continue
        if st == "fail":
            i += 1
            time.sleep(2)
            continue
        if rows:
            have = {r[1] for r in rev[c]}
            add = [r for r in rows if r[1] not in have]
            if add:
                rev[c].extend(add)
                rev[c].sort(key=lambda r: r[0])
                upd += 1
                new_rows += len(add)
        i += 1
        time.sleep(SLEEP_OK)
        if i % SAVE_EVERY == 0:
            _save(rev, path)
            logger.info(f"[revenue] {i}/{len(codes)} 檔已掃,更新 {upd} 檔 +{new_rows} 月")
    _save(rev, path)
    logger.info(f"[revenue] 完成:更新 {upd} 檔,新增 {new_rows} 月 → {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只檢測不抓")
    ap.add_argument("--force", action="store_true", help="不管新舊都增量更新")
    ap.add_argument("--kline-source", choices=("twse", "finmind"), default="twse",
                    help="kline 來源。預設 twse(免 token、一請求一天全市場);"
                         "finmind 是舊路徑,雲端連不上時會無聲磨數十小時")
    args = ap.parse_args()

    kp, rp = config.KLINE_PATH, config.REVENUE_PATH
    if not os.path.exists(kp) or not os.path.exists(rp):
        config.require_data()
    deep = json.load(open(kp, encoding="utf-8"))
    rev = json.load(open(rp, encoding="utf-8"))

    k_stale, k_max, k_target, k_behind, k_total = kline_status(deep)
    r_stale, r_latest, r_expected = revenue_status(rev)

    print("=" * 64)
    print(f"資料家:{config.DATA_DIR}  價格模式:{config.PRICE_MODE}({config.PRICE_DATASET})")
    print(f"kline   最新 {k_max}  應到 {k_target}  落後 {k_behind}/{k_total} 檔"
          f"  → {'⚠️ 過期' if k_stale else '✅ 最新'}")
    print(f"revenue 最新 {r_latest}  應到 {r_expected}  → {'⚠️ 過期' if r_stale else '✅ 最新'}")
    print("=" * 64)

    if args.check:
        return

    do_k = args.force or k_stale
    do_r = args.force or r_stale
    if not (do_k or do_r):
        print("都最新,免更新。")
        return
    # kline 走 TWSE 不需要 token;只有 revenue(FinMind 獨有)才需要。
    needs_token = do_r or (do_k and args.kline_source == "finmind")
    if needs_token and not FINMIND_TOKEN:
        logger.error("需更新但無 FINMIND_TOKEN。export FINMIND_TOKEN=... 後重跑。"
                     "(kline 走 --kline-source twse 則不需要 token)")
        if not do_k or args.kline_source == "finmind":
            return
        do_r = False

    if do_k:
        logger.info(f"kline 增量更新(落後 {k_behind} 檔 → 補到今天,"
                    f"來源 {args.kline_source})…")
        if args.kline_source == "twse":
            update_kline_twse(deep, kp)
        else:
            update_kline(deep, kp)
    if do_r:
        logger.info("revenue 增量更新(補新月份)…")
        update_revenue(rev, rp)
    print("更新完成。")


if __name__ == "__main__":
    main()
