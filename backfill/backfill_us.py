#!/usr/bin/env python3
"""
美股日K回填(跨市場驗證用)。

用途:台股測到的因子,如果在美股也成立 → 不是台股樣本的偶然;
      只在台股成立 → 高度可疑(單一市場、單一樣本期的產物)。

清單來源:FinMind USStockInfo(取成交量前 N 檔,避免抓到沒流動性的殼)。
輸出格式與台股一致 {sid:[{date,open,max,min,close,volume}]},可直接餵 framework。

用法:
  export FINMIND_TOKEN=...
  python backfill/backfill_us.py --top 300 --start 2010-01-01
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from framework import config
from framework.finmind import http_get, get_logger, FINMIND_TOKEN, FINMIND_API

logger = get_logger("backfill_us")
SAVE_EVERY, SLEEP_OK, HOUR_BUFFER, FAIL_STOP = 50, 0.4, 120, 8


def _secs_to_next_hour():
    now = datetime.now()
    nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return int((nxt - now).total_seconds()) + HOUR_BUFFER


def us_symbols(top):
    """從 FinMind 取美股清單;失敗則退回內建大型股清單。"""
    try:
        j = http_get(FINMIND_API, params={"dataset": "USStockInfo",
                                          "token": FINMIND_TOKEN}, timeout=60).json()
        if j.get("status") == 200 and j.get("data"):
            syms = []
            seen = set()
            for x in j["data"]:
                s = (x.get("stock_id") or "").strip().upper()
                # 只留純字母代號(排除權證/優先股/ETF 的點號與數字型代號)
                if s and s.isalpha() and 1 <= len(s) <= 5 and s not in seen:
                    seen.add(s)
                    syms.append(s)
            if syms:
                logger.info(f"USStockInfo 取得 {len(syms)} 檔,取前 {top}")
                return syms[:top]
    except Exception as e:
        logger.warning(f"USStockInfo 失敗:{e}")
    logger.warning("退回內建清單")
    return ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "JPM", "JNJ", "V",
            "PG", "UNH", "HD", "MA", "BAC", "XOM", "CVX", "ABBV", "PFE", "KO",
            "PEP", "COST", "WMT", "MRK", "TMO", "CSCO", "ACN", "MCD", "ABT", "DHR"][:top]


def fetch(sid, start, end):
    try:
        j = http_get(FINMIND_API, params={"dataset": "USStockPrice", "data_id": sid,
                     "start_date": start, "end_date": end, "token": FINMIND_TOKEN},
                     timeout=60).json()
    except Exception:
        return [], "fail"
    st, msg = j.get("status"), str(j.get("msg", ""))
    if st != 200:
        return [], "quota" if (st == 402 or "upper limit" in msg.lower()) else "fail"
    out = []
    for x in j.get("data", []):
        try:
            out.append({"date": x["date"], "open": float(x["Open"]), "max": float(x["High"]),
                        "min": float(x["Low"]), "close": float(x["Close"]),
                        "volume": float(x.get("Volume") or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    return out, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=300)
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--out", default=os.path.join(config.DATA_DIR, "us_kline.json"))
    a = ap.parse_args()
    if not FINMIND_TOKEN:
        logger.error("無 FINMIND_TOKEN。")
        return

    data = {}
    if os.path.exists(a.out):
        try:
            data = json.load(open(a.out, encoding="utf-8"))
            logger.info(f"resume:已有 {len(data)} 檔")
        except Exception:
            data = {}

    syms = us_symbols(a.top)
    logger.info(f"目標 {len(syms)} 檔  區間 {a.start}~{a.end}")

    def save():
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        tmp = a.out + ".tmp"
        json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False,
                  separators=(",", ":"))
        os.replace(tmp, a.out)

    done = skip = fail = consec = i = 0
    while i < len(syms):
        s = syms[i]
        if s in data and data[s]:
            skip += 1; i += 1; continue
        bars, st = fetch(s, a.start, a.end)
        if st == "quota":
            save(); w = _secs_to_next_hour()
            logger.warning(f"配額爆({len(data)}/{len(syms)})→ 睡 {w//60} 分續跑…")
            time.sleep(w); continue
        if st == "fail":
            fail += 1; consec += 1
            if consec >= FAIL_STOP:
                logger.error(f"連續 {FAIL_STOP} 檔失敗→自停(已存 {len(data)})"); save(); return
            time.sleep(5); i += 1; continue
        consec = 0
        if len(bars) > 200:          # 太短的(新上市/資料破碎)不收
            data[s] = bars; done += 1
        i += 1; time.sleep(SLEEP_OK)
        if i % SAVE_EVERY == 0:
            save(); logger.info(f"進度 {i}/{len(syms)}:新增 {done} 跳過 {skip} 失敗 {fail}")
    save()
    logger.info(f"完成:新增 {done} 跳過 {skip} 失敗 {fail},總 {len(data)} 檔 → {a.out}")


if __name__ == "__main__":
    main()
