#!/usr/bin/env python3
"""
合成市場產生器 —— 測試「驗證器本身」用的假資料。

不碰真資料、不用網路:產一個已知答案的宇宙,框架的結論必須跟答案一致:
  planted_universe()  10 檔贏家有真 edge(正漂移),其餘無 → 框架該亮綠
  random_universe()   全部是（各自 seed 的）隨機漫步,無任何 edge → 框架該亮紅/平
"""
import json
import os
import random
import tempfile
from datetime import date, timedelta

BENCH = "0050"


def trading_days(start="2022-06-01", n=560):
    """平日序列(合成日曆,跳過六日)。560 天 ≈ 橫跨 2022-2024,含 OOS 切點。"""
    out = []
    d = date.fromisoformat(start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _bars(dates, closes):
    return [{"date": dt, "open": c, "max": c, "min": c, "close": round(c, 4),
             "volume": 1_000_000} for dt, c in zip(dates, closes)]


def planted_universe(n_stocks=50, n_winners=10, n_days=560, drift=0.002):
    """
    已知答案的宇宙:
      W00~W09  贏家:每日 +drift(複利),真 edge
      S10~S49  一般:價格平(100)
      0050     基準:緩漲(regime 恆為多頭,不干擾)
    回 (kline_dict, winners_set)。
    """
    dates = trading_days(n=n_days)
    d = {}
    winners = set()
    for i in range(n_stocks):
        sid = f"W{i:02d}" if i < n_winners else f"S{i:02d}"
        if i < n_winners:
            winners.add(sid)
            closes = [100 * (1 + drift) ** t for t in range(len(dates))]
        else:
            closes = [100.0] * len(dates)
        d[sid] = _bars(dates, closes)
    d[BENCH] = _bars(dates, [100 * 1.0003 ** t for t in range(len(dates))])
    return d, winners


def random_universe(n_stocks=50, n_days=560, sigma=0.01):
    """全隨機漫步宇宙(各檔獨立 seed):任何訊號都不該有 edge;偷看未來的才會賺。"""
    dates = trading_days(n=n_days)
    d = {}
    for i in range(n_stocks):
        sid = f"R{i:02d}"
        rng = random.Random(1000 + i)
        c = 100.0
        closes = []
        for _ in dates:
            c *= 1 + rng.gauss(0, sigma)
            closes.append(max(c, 1.0))
        d[sid] = _bars(dates, closes)
    d[BENCH] = _bars(dates, [100 * 1.0003 ** t for t in range(len(dates))])
    return d


def write_kline(d) -> str:
    """寫進暫存檔,回路徑(Data 吃 kline_path)。"""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                    encoding="utf-8")
    json.dump(d, f, ensure_ascii=False)
    f.close()
    return f.name


def cleanup(path):
    try:
        os.unlink(path)
    except OSError:
        pass
