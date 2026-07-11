#!/usr/bin/env python3
"""
免贊助版還原價:raw kline + 免費事件資料集 → kline_deep_adj.json(前復權)。

為什麼存在:TaiwanStockPriceAdj 是贊助會員限定(register 級 400)。
但三個事件資料集免費:
  TaiwanStockDividendResult              除權息(before/after 價 → 精確因子)
  TaiwanStockSplitPrice                  股票分割
  TaiwanStockCapitalReductionReferencePrice  減資

流程(可中斷 resume,事件存 data/adj_events.json):
  1. 全檔逐股拉 DividendResult(除權息人人有,~1083 call)
  2. 用除權息修正後,掃殘餘單日 |漲跌|>11% 的股票(台股漲跌停 10%,
     超過=分割/減資/資料錯)→ 只對這批候選拉 Split + CapitalReduction(省配額)
  3. 由事件 before/after 算因子,由後往前累積回調 open/max/min/close
     (volume 不動:僅作規模代理);候選事件缺官方資料 → 用當日 close 比值
     當因子(帶小量盤中誤差,標記 fallback)
  4. 寫 kline_deep_adj.json(schema 與 raw 相同)+ 殘餘異常報告

用法:
  export FINMIND_TOKEN=...
  python backfill/build_adj.py            # 全流程
  python backfill/build_adj.py --report   # 只印殘餘異常(不抓)
撞配額自動睡到整點;與其他 backfill 同風格。
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from framework import config
from framework.finmind import http_get, get_logger, FINMIND_TOKEN, FINMIND_API

logger = get_logger("build_adj")
RAW_PATH = os.path.join(config.DATA_DIR, "kline_deep.json")
ADJ_PATH = os.path.join(config.DATA_DIR, "kline_deep_adj.json")
EVENTS_PATH = os.path.join(config.DATA_DIR, "adj_events.json")
JUMP_TH = 0.11          # 殘餘跳動門檻(>漲跌停10%)
SLEEP_OK, SAVE_EVERY, HOUR_BUFFER = 0.4, 50, 120

DIV = "TaiwanStockDividendResult"
SPLIT = "TaiwanStockSplitPrice"
REDUC = "TaiwanStockCapitalReductionReferencePrice"


def _secs_to_next_hour():
    now = datetime.now()
    nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return int((nxt - now).total_seconds()) + HOUR_BUFFER


def fetch_events(dataset, sid, start):
    """回 (rows, status)。rows=[(date, before, after)];status ok/quota/fail。"""
    try:
        j = http_get(FINMIND_API, params={"dataset": dataset, "data_id": sid,
                     "start_date": start, "token": FINMIND_TOKEN}, timeout=40).json()
    except Exception:
        return [], "fail"
    st, msg = j.get("status"), str(j.get("msg", ""))
    if st != 200:
        return [], "quota" if (st == 402 or "upper limit" in msg.lower()) else "fail"
    rows = []
    for x in j.get("data", []):
        b, a = x.get("before_price"), x.get("after_price") or x.get("reference_price")
        if b and a and b > 0 and a > 0:
            rows.append((x["date"], float(b), float(a)))
    return rows, "ok"


def fetch_all(deep, events, datasets_by_sid, start):
    """datasets_by_sid = {sid: [dataset,...]} 待抓清單;寫進 events{sid:{dataset:rows}}。"""
    todo = [(s, ds) for s, dss in datasets_by_sid.items() for ds in dss
            if ds not in events.get(s, {})]
    logger.info(f"待抓 {len(todo)} 筆(股×資料集)")
    done = 0
    for sid, ds in todo:
        while True:
            rows, st = fetch_events(ds, sid, start)
            if st == "quota":
                _save_events(events)
                w = _secs_to_next_hour()
                logger.warning(f"配額爆({done}/{len(todo)})→ 睡 {w//60} 分…")
                time.sleep(w)
                continue
            break
        if st == "ok":
            events.setdefault(sid, {})[ds] = rows
        done += 1
        time.sleep(SLEEP_OK)
        if done % SAVE_EVERY == 0:
            _save_events(events)
            logger.info(f"進度 {done}/{len(todo)}")
    _save_events(events)


def _save_events(events):
    tmp = EVENTS_PATH + ".tmp"
    json.dump(events, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, EVENTS_PATH)


def residual_jumps(bars, factors, skip_head=10):
    """套用 factors 後仍 >JUMP_TH 的 (date, ratio) 清單。factors={date: f}。
    skip_head:跳過掛牌頭 N 根(IPO 前五日無漲跌停,大漲是真的,不是事件)。"""
    out = []
    cum = _cum_factors(bars, factors)
    for i in range(max(1, skip_head), len(bars)):
        # 兩端都要有真價:冷門股無成交日 close=0,比出來的 -100% 是假事件,
        # 拿去當因子會把整段歷史乘成 0(2026-07-12 修)。
        p0, p1 = bars[i - 1]["close"] * cum[i - 1], bars[i]["close"] * cum[i]
        if p0 > 0 and p1 > 0 and abs(p1 / p0 - 1) > JUMP_TH:
            out.append((bars[i]["date"], round(p1 / p0 - 1, 3)))
    return out


def _cum_factors(bars, factors):
    """每根的累積前復權因子(事件日之前的根 ×factor)。factors={事件date: f}。
    f = after/before,事件日當天視為新價,之前全乘。由後往前累積。"""
    n = len(bars)
    cum = [1.0] * n
    acc = 1.0
    fs = sorted(factors.items())          # [(date, f)] 升序
    fi = len(fs) - 1
    for i in range(n - 1, -1, -1):
        while fi >= 0 and bars[i]["date"] < fs[fi][0]:
            acc *= fs[fi][1]
            fi -= 1
        cum[i] = acc
    # 收尾:比最早 bar 還早的事件(理論上沒有)忽略
    return cum


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="只印殘餘異常,不抓不寫")
    args = ap.parse_args()

    deep = json.load(open(RAW_PATH, encoding="utf-8"))
    start = min(bars[0]["date"] for bars in deep.values() if bars)
    events = {}
    if os.path.exists(EVENTS_PATH):
        events = json.load(open(EVENTS_PATH, encoding="utf-8"))
        logger.info(f"resume:已有 {len(events)} 檔事件")

    if not args.report:
        if not FINMIND_TOKEN:
            logger.error("無 FINMIND_TOKEN。")
            return
        # 第 1 波:全檔除權息
        fetch_all(deep, events, {s: [DIV] for s in deep}, start)

        # 第 2 波:除權息修正後仍有殘餘跳動的 → 拉分割+減資
        cand = []
        for sid, bars in deep.items():
            fs = {d: a / b for d, b, a in events.get(sid, {}).get(DIV, [])}
            if residual_jumps(bars, fs):
                cand.append(sid)
        logger.info(f"殘餘跳動候選 {len(cand)} 檔 → 拉分割/減資")
        fetch_all(deep, events, {s: [SPLIT, REDUC] for s in cand}, start)

    # 合成因子 → 寫 adj
    fallback = []
    residual_report = []
    adj = {}
    for sid, bars in deep.items():
        ev = events.get(sid, {})
        factors = {}
        for ds in (DIV, SPLIT, REDUC):
            for d, b, a in ev.get(ds, []):
                factors[d] = factors.get(d, 1.0) * (a / b)
        # 官方事件套完仍殘餘 → 當日 close 比值當因子(fallback,帶盤中誤差)
        for d, r in residual_jumps(bars, factors):
            factors[d] = factors.get(d, 1.0) * (1 + r)
            fallback.append((sid, d, r))
        cum = _cum_factors(bars, factors)
        nb = []
        for i, b in enumerate(bars):
            f = cum[i]
            nb.append({"date": b["date"], "open": b["open"] * f, "max": b["max"] * f,
                       "min": b["min"] * f, "close": b["close"] * f, "volume": b["volume"]})
        adj[sid] = nb
        rj = residual_jumps(nb, {})
        if rj:
            residual_report.append((sid, rj[:3]))

    if args.report:
        print(f"fallback 因子 {len(fallback)} 筆;殘餘異常 {len(residual_report)} 檔")
        return

    tmp = ADJ_PATH + ".tmp"
    json.dump(adj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, ADJ_PATH)
    logger.info(f"完成:{len(adj)} 檔 → {ADJ_PATH}")
    logger.info(f"fallback 因子(無官方事件,用跳動比值){len(fallback)} 筆")
    if fallback[:10]:
        logger.info(f"fallback 樣本:{fallback[:10]}")
    logger.info(f"還原後殘餘 >11% 跳動:{len(residual_report)} 檔(理論上=0,>0 表示還有沒抓到的事件)")


if __name__ == "__main__":
    main()
