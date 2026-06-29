#!/usr/bin/env python3
"""
法人 T86 逐日回填(選用,給法人濾網用)。寫 t86_cache/YYYYMMDD.json {code:[外資,投信,自營]}(股數)。
欄位索引:外資=4(不含自營)、投信=10、自營=11。已存在日期直接跳過,可中斷重跑。

用法:
  python backfill/backfill_t86.py --days 365
  python backfill/backfill_t86.py --start 20210101 --end 20251231
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from framework import config
from framework.finmind import http_get, get_logger

logger = get_logger("backfill_t86")
T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86?date={ds}&selectType=ALL&response=json"
SLEEP_OK, SLEEP_BACKOFF = 3.0, 30


def _num(x):
    try:
        return float(str(x).replace(",", "").strip() or "0")
    except Exception:
        return 0.0


def fetch_one_day(ds):
    body = None
    for attempt in range(3):
        try:
            body = http_get(T86_URL.format(ds=ds), timeout=25).json()
            break
        except Exception as e:
            logger.warning(f"{ds} 第 {attempt+1} 次失敗:{e}")
            time.sleep(SLEEP_BACKOFF)
    if body is None:
        return None
    if body.get("stat") != "OK" or not body.get("data"):
        return {}
    rows = {}
    for row in body["data"]:
        code = row[0].strip()
        if code.isdigit() and len(code) == 4:
            rows[code] = [_num(row[4]), _num(row[10]), _num(row[11])]
    return rows


def iter_dates(start, end):
    d = end
    while d >= start:
        if d.weekday() < 5:
            yield d
        d -= timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--start")
    ap.add_argument("--end")
    args = ap.parse_args()
    end = datetime.strptime(args.end, "%Y%m%d") if args.end else datetime.now()
    start = datetime.strptime(args.start, "%Y%m%d") if args.start else end - timedelta(days=args.days)

    os.makedirs(config.T86_DIR, exist_ok=True)
    written = skipped = empty = failed = 0
    for d in iter_dates(start, end):
        ds = d.strftime("%Y%m%d")
        path = os.path.join(config.T86_DIR, f"{ds}.json")
        if os.path.exists(path):
            skipped += 1; continue
        rows = fetch_one_day(ds)
        if rows is None:
            failed += 1; continue
        if not rows:
            empty += 1; time.sleep(SLEEP_OK); continue
        json.dump(rows, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        written += 1
        logger.info(f"{ds} ✓ {len(rows)} 檔")
        time.sleep(SLEEP_OK)
    logger.info(f"完成:新增 {written} 跳過 {skipped} 無資料 {empty} 失敗 {failed}")


if __name__ == "__main__":
    main()
