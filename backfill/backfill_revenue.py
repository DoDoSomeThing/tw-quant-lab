#!/usr/bin/env python3
"""
全市場月營收回填。寫 revenue.json {sid:[[avail_date, yyyymm, revenue], ...]}。
avail_date = 發布+11天(保守跨過每月10號公布,避免前視)。
可中斷 resume;撞配額睡到整點續跑。

用法:
  export FINMIND_TOKEN=...
  python backfill/backfill_revenue.py
"""
import os
import sys
import json
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from framework import config
from framework.finmind import http_get, get_tse_stock_list, get_logger, FINMIND_TOKEN, FINMIND_API

logger = get_logger("backfill_revenue")
START, END = "2019-01-01", "2026-12-31"   # 2019 起 → 2020 事件也有 YoY 基期
SAVE_EVERY, SLEEP_OK, HOUR_BUFFER = 50, 0.35, 120


def _secs_to_next_hour():
    now = datetime.now()
    nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return int((nxt - now).total_seconds()) + HOUR_BUFFER


def fetch(sid):
    try:
        j = http_get(FINMIND_API, params={"dataset": "TaiwanStockMonthRevenue", "data_id": sid,
                     "start_date": START, "end_date": END, "token": FINMIND_TOKEN}, timeout=40).json()
    except Exception:
        return [], "fail"
    st = j.get("status")
    if st != 200:
        if st == 402 or "upper limit" in str(j.get("msg", "")).lower():
            return [], "quota"
        return [], "fail"
    rows = []
    for x in j.get("data", []):
        ym = x["revenue_year"] * 100 + x["revenue_month"]
        avail = (datetime.strptime(x["date"], "%Y-%m-%d") + timedelta(days=11)).strftime("%Y-%m-%d")
        rows.append([avail, ym, float(x["revenue"])])
    return sorted(rows), "ok"


def main():
    out = config.REVENUE_PATH
    if not FINMIND_TOKEN:
        logger.error("無 FINMIND_TOKEN。export FINMIND_TOKEN=... 後重跑。")
        return
    data = {}
    if os.path.exists(out):
        data = json.load(open(out, encoding="utf-8"))
        logger.info(f"resume:已有 {len(data)} 檔")
    codes = [c for c in get_tse_stock_list() if c.isdigit() and len(c) == 4]
    logger.info(f"目標 {len(codes)} 檔")

    def save():
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        tmp = out + ".tmp"
        json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, out)

    done = skip = fail = consec = i = 0
    while i < len(codes):
        c = codes[i]
        if c in data:
            skip += 1; i += 1; continue
        rows, st = fetch(c)
        if st == "quota":
            save(); w = _secs_to_next_hour()
            logger.warning(f"配額爆({len(data)}/{len(codes)})→ 睡 {w//60} 分到整點續跑…")
            time.sleep(w); continue
        if st == "fail":
            fail += 1; consec += 1
            if consec >= 8:
                logger.error(f"連續8檔失敗→自停(已存 {len(data)})"); save(); return
            time.sleep(20); i += 1; continue
        consec = 0
        if rows:
            data[c] = rows; done += 1
        i += 1; time.sleep(SLEEP_OK)
        if i % SAVE_EVERY == 0:
            save(); logger.info(f"進度 {i}/{len(codes)}:新增 {done} 跳過 {skip} 失敗 {fail}")
    save()
    logger.info(f"完成:新增 {done} 跳過 {skip} 失敗 {fail},總 {len(data)} 檔 → {out}")


if __name__ == "__main__":
    main()
