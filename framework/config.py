#!/usr/bin/env python3
"""
全域設定。資料路徑、成本、基準、樣本外切點都集中在這。

資料策略:資料不進 git。用自己的 FinMind token 跑 backfill/ 把資料抓進 data/,
或用環境變數指向已有資料目錄:
  QLAB_DATA_DIR  kline_deep.json / revenue.json 所在目錄(預設 ./data)
  QLAB_T86_DIR   法人買賣超(t86)逐日 json 目錄(預設 ./data/t86_cache)
"""
import os

# repo 根目錄(framework/ 的上一層)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 預設資料目錄:repo 內的 data/(不進 git;backfill 寫這、或用 env 指向他處)。
_DEFAULT_DATA = os.path.join(ROOT, "data")
_DEFAULT_T86 = os.path.join(ROOT, "data", "t86_cache")

DATA_DIR = os.environ.get("QLAB_DATA_DIR", _DEFAULT_DATA)
T86_DIR = os.environ.get("QLAB_T86_DIR", _DEFAULT_T86)

# 價格模式:raw=原始價(TaiwanStockPrice)、adj=還原價(kline_deep_adj.json,
# 由 backfill/build_adj.py 用免費事件資料集自建;TaiwanStockPriceAdj 是贊助會員限定)。
# 原始價有兩個已知偏差:①分割/減資造成假跳動(污染動能類訊號)②除息假跌 →
# 高殖利率/存股型訊號的報酬被系統性低估。驗這類訊號請用 adj。
PRICE_MODE = os.environ.get("QLAB_PRICE", "raw").lower()
if PRICE_MODE not in ("raw", "adj"):
    raise SystemExit(f"QLAB_PRICE={PRICE_MODE} 不合法,只接受 raw / adj。")
PRICE_DATASET = "TaiwanStockPriceAdj" if PRICE_MODE == "adj" else "TaiwanStockPrice"

# 檔名預設跟著價格模式走(raw/adj 兩套並存不互污);要並存更多份資料
# (例:長歷史版 kline_deep_long.json)再用 QLAB_KLINE_FILE 明確覆蓋。
_DEFAULT_KLINE = "kline_deep_adj.json" if PRICE_MODE == "adj" else "kline_deep.json"
KLINE_FILE = os.environ.get("QLAB_KLINE_FILE", _DEFAULT_KLINE)
REVENUE_FILE = os.environ.get("QLAB_REVENUE_FILE", "revenue.json")

KLINE_PATH = os.path.join(DATA_DIR, KLINE_FILE)
REVENUE_PATH = os.path.join(DATA_DIR, REVENUE_FILE)

# 基準與成本
BENCH = "0050"
COST = 0.006              # 來回成本(基準)
COST_LO, COST_HI = 0.004, 0.008

# 樣本外切點(第二關)
OOS_FROM = "2024-01-01"   # 早期研究的 IS/OOS 切點
OOS2_FROM = "2025-01-01"  # 真·樣本外(今天用 2025 一翻就破)

# regime
REGIME_MA = 120           # 0050 站上 MA120 = 非空頭


def require_data():
    """檢查資料檔在不在,不在就給清楚指示(別讓 study 噴 FileNotFoundError)。"""
    missing = [p for p in (KLINE_PATH, REVENUE_PATH) if not os.path.exists(p)]
    if missing:
        raise SystemExit(
            "找不到資料檔:\n  " + "\n  ".join(missing) +
            "\n\n設 QLAB_DATA_DIR 指向 kline_deep.json/revenue.json 所在目錄,"
            "或跑 backfill/ 抓資料。\n目前 QLAB_DATA_DIR=" + DATA_DIR
        )
