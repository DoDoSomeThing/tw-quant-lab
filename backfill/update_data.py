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
        j = http_get(FINMIND_API, params={"dataset": "TaiwanStockPrice", "data_id": sid,
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
    args = ap.parse_args()

    kp, rp = config.KLINE_PATH, config.REVENUE_PATH
    if not os.path.exists(kp) or not os.path.exists(rp):
        config.require_data()
    deep = json.load(open(kp, encoding="utf-8"))
    rev = json.load(open(rp, encoding="utf-8"))

    k_stale, k_max, k_target, k_behind, k_total = kline_status(deep)
    r_stale, r_latest, r_expected = revenue_status(rev)

    print("=" * 64)
    print(f"資料家:{config.DATA_DIR}")
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
    if not FINMIND_TOKEN:
        logger.error("需更新但無 FINMIND_TOKEN。export FINMIND_TOKEN=... 後重跑。")
        return

    if do_k:
        logger.info(f"kline 增量更新(落後 {k_behind} 檔 → 補到今天)…")
        update_kline(deep, kp)
    if do_r:
        logger.info("revenue 增量更新(補新月份)…")
        update_revenue(rev, rp)
    print("更新完成。")


if __name__ == "__main__":
    main()
