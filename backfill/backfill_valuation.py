#!/usr/bin/env python3
"""
估值回填(PE / PB / 殖利率)—— 給 Phase 2「便宜是不是買進理由」用。

只抓**再平衡日當天**(不是每個交易日),日期清單直接由 framework/search.build_panel
的同一條規則算出來(range(warmup, n-maxh, rebalance)),確保與面板逐日對齊、不錯位。

資料源:TWSE BWIBBU_d,一個請求拿全市場一天,免 token 免配額。
⚠️ 只含**上市**;上櫃需 TPEx 另一支端點 → 結果須註明樣本範圍。
⚠️ 欄位 schema 有兩版,必須依欄名解析不能用固定 index:
     2017 以前(5 欄):證券代號 證券名稱 本益比 殖利率(%) 股價淨值比
     2018 以後(8 欄):證券代號 證券名稱 收盤價 殖利率(%) 股利年度 本益比 股價淨值比 財報年/季
⚠️ 虧損股本益比是 "-" → 存 None。切估值請用 PB(PE 大量缺值且分母趨零會爆)。

輸出:data/valuation/YYYYMMDD.json = {code: {"pe":x|null, "pb":y|null, "yield":z|null}}
可中斷 resume(已存在的日期直接跳過)。

用法:
  export QLAB_KLINE_FILE=kline_deep_long.json
  python backfill/backfill_valuation.py
"""
import os
import sys
import json
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import urllib.request
import urllib.error

from framework import config
from framework.data import Data

# SPEC §6 指定路徑;本機網段若被 WAF 擋 307 則退回舊路徑(見 daily 2026-08-06)
URLS = [
    "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date={d}&selectType=ALL&response=json",
    "https://www.twse.com.tw/exchangeReport/BWIBBU_d?date={d}&selectType=ALL&response=json",
]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 tw-quant-lab/1.0"

ap = argparse.ArgumentParser()
ap.add_argument("--rebalance", type=int, default=21)
ap.add_argument("--warmup", type=int, default=252)
ap.add_argument("--maxh", type=int, default=60)
ap.add_argument("--sleep", type=float, default=3.0)
ap.add_argument("--out", default=os.path.join(config.DATA_DIR, "valuation"))
args = ap.parse_args()


def num(s):
    """'27.10' → 27.1;'-' / '' / None → None。"""
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    if s in ("", "-", "--", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse(payload):
    """依**欄名**解析,兩種 schema 通吃。回 {code: {pe, pb, yield}}。"""
    fields = payload.get("fields") or []
    rows = payload.get("data") or []
    idx = {name: i for i, name in enumerate(fields)}
    i_code = idx.get("證券代號")
    i_pe = idx.get("本益比")
    i_pb = idx.get("股價淨值比")
    i_yld = idx.get("殖利率(%)")
    if i_code is None or i_pb is None:
        raise ValueError(f"欄位對不上,fields={fields}")
    out = {}
    for r in rows:
        try:
            code = str(r[i_code]).strip()
        except (IndexError, TypeError):
            continue
        if not code:
            continue
        out[code] = {
            "pe": num(r[i_pe]) if i_pe is not None and i_pe < len(r) else None,
            "pb": num(r[i_pb]) if i_pb < len(r) else None,
            "yield": num(r[i_yld]) if i_yld is not None and i_yld < len(r) else None,
        }
    return out


def fetch(ymd):
    last = None
    for tmpl in URLS:
        url = tmpl.format(d=ymd)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as e:                      # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            continue
        stat = payload.get("stat")
        if stat != "OK":
            last = f"stat={stat}"
            continue
        return parse(payload), None
    return None, last


def main():
    os.makedirs(args.out, exist_ok=True)

    print(f"載入 K 線取交易日曆({config.KLINE_FILE})...")
    data = Data()
    cal = data.cal
    n = len(cal)
    # 與 framework/search.build_panel 同一條規則,不可改
    reb = [i for i in range(args.warmup, n - args.maxh, args.rebalance)]
    dates = [cal[i] for i in reb]
    print(f"交易日曆 {n} 天 {cal[0]}~{cal[-1]} → 再平衡日 {len(dates)} 個 "
          f"{dates[0]}~{dates[-1]}")

    todo = []
    for dt in dates:
        ymd = dt.replace("-", "")
        if not os.path.exists(os.path.join(args.out, f"{ymd}.json")):
            todo.append((dt, ymd))
    print(f"已存在 {len(dates)-len(todo)} 個,待抓 {len(todo)} 個 "
          f"(約 {len(todo)*args.sleep/60:.1f} 分鐘)\n")

    ok = fail = 0
    for k, (dt, ymd) in enumerate(todo, 1):
        rec, err = fetch(ymd)
        if rec is None:
            fail += 1
            print(f"[{k}/{len(todo)}] {dt} ❌ {err}")
        else:
            with open(os.path.join(args.out, f"{ymd}.json"), "w",
                      encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)
            pb = sum(1 for v in rec.values() if v["pb"] is not None)
            pe = sum(1 for v in rec.values() if v["pe"] is not None)
            ok += 1
            print(f"[{k}/{len(todo)}] {dt} ✓ {len(rec)} 檔  PB {pb}  PE {pe}")
        time.sleep(args.sleep)

    print(f"\n完成:成功 {ok}、失敗 {fail}、跳過 {len(dates)-len(todo)}")
    if fail:
        print("⚠️ 有失敗日期,重跑本腳本會只補失敗的那幾天(已成功的會跳過)")


if __name__ == "__main__":
    main()
