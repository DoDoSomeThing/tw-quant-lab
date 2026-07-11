#!/usr/bin/env python3
"""
深歷史日K回填(全市場,不砍根數)。寫 kline_deep.json {sid:[{date,open,max,min,close,volume}]}。
可中斷 resume;撞 FinMind 配額(402)睡到整點重置續跑;連續網路失敗自停。

用法:
  export FINMIND_TOKEN=...
  python backfill/backfill_kline.py --start 2021-06-01 --end 2025-12-31
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from framework import config
from framework.finmind import http_get, get_tse_stock_list, get_logger, FINMIND_TOKEN, FINMIND_API

logger = get_logger("backfill_kline")
SAVE_EVERY, SLEEP_OK, SLEEP_BACKOFF, FAIL_STOP, HOUR_BUFFER = 50, 0.4, 30, 8, 120


def _secs_to_next_hour():
    now = datetime.now()
    nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return int((nxt - now).total_seconds()) + HOUR_BUFFER


def fetch_deep(sid, start, end):
    try:
        j = http_get(FINMIND_API, params={"dataset": config.PRICE_DATASET, "data_id": sid,
                     "start_date": start, "end_date": end, "token": FINMIND_TOKEN}, timeout=40).json()
    except Exception as e:
        logger.warning(f"{sid} 例外:{e}")
        return [], "fail"
    st, msg = j.get("status"), str(j.get("msg", ""))
    if st != 200:
        if st == 402 or "upper limit" in msg.lower():
            return [], "quota"
        return [], "fail"
    return [{"date": x["date"], "open": float(x["open"]), "max": float(x["max"]),
             "min": float(x["min"]), "close": float(x["close"]),
             "volume": float(x["Trading_Volume"])} for x in j.get("data", [])], "ok"


def covered(bars, start, end):
    return bool(bars) and bars[0]["date"][:7] <= start[:7] and bars[-1]["date"][:7] >= end[:7]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-06-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--out", default=config.KLINE_PATH)
    args = ap.parse_args()
    if not FINMIND_TOKEN:
        logger.error("無 FINMIND_TOKEN。export FINMIND_TOKEN=... 後重跑。")
        return

    deep = {}
    if os.path.exists(args.out):
        try:
            deep = json.load(open(args.out, encoding="utf-8"))
            logger.info(f"resume:已有 {len(deep)} 檔")
        except Exception:
            deep = {}
    codes = [c for c in get_tse_stock_list() if c.isdigit() and len(c) == 4]
    logger.info(f"目標 {len(codes)} 檔  區間 {args.start}~{args.end}"
                f"  資料集 {config.PRICE_DATASET}({config.PRICE_MODE}) → {args.out}")

    def save():
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        tmp = args.out + ".tmp"
        json.dump(deep, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, args.out)

    done = skip = fail = consec = i = 0
    while i < len(codes):
        c = codes[i]
        if covered(deep.get(c, []), args.start, args.end):
            skip += 1; i += 1; continue
        bars, st = fetch_deep(c, args.start, args.end)
        if st == "quota":
            save(); w = _secs_to_next_hour()
            logger.warning(f"配額爆({len(deep)}/{len(codes)})→ 睡 {w//60} 分到整點續跑…")
            time.sleep(w); continue
        if st == "fail":
            fail += 1; consec += 1
            if consec >= FAIL_STOP:
                logger.error(f"連續 {FAIL_STOP} 檔失敗→自停(已存 {len(deep)})"); save(); return
            time.sleep(SLEEP_BACKOFF); i += 1; continue
        consec = 0
        if bars:
            deep[c] = bars; done += 1
        i += 1; time.sleep(SLEEP_OK)
        if i % SAVE_EVERY == 0:
            save(); logger.info(f"進度 {i}/{len(codes)}:新增 {done} 跳過 {skip} 失敗 {fail}")
    save()
    logger.info(f"完成:新增 {done} 跳過 {skip} 失敗 {fail},總 {len(deep)} 檔 → {args.out}")


if __name__ == "__main__":
    main()
