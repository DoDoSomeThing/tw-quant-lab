#!/usr/bin/env python3
"""
全域設定。資料路徑、成本、基準、樣本外切點都集中在這。

資料策略:預設指向 tw-stock-bot 的 cache(資料只放一份,不複製、不進 git)。
公開版讓使用者用自己的 FinMind token 跑 backfill/ 把資料抓進 data/。
全部可用環境變數覆蓋:
  QLAB_DATA_DIR  kline_deep.json / revenue.json 所在目錄
  QLAB_T86_DIR   法人買賣超(t86)逐日 json 目錄
"""
import os

# repo 根目錄(framework/ 的上一層)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 預設資料源:同層的 tw-stock-bot/cache(不複製,單一真相)。
_DEFAULT_DATA = os.path.normpath(os.path.join(ROOT, "..", "tw-stock-bot", "cache"))
_DEFAULT_T86 = os.path.normpath(os.path.join(ROOT, "..", "tw-stock-bot", "t86_cache"))

DATA_DIR = os.environ.get("QLAB_DATA_DIR", _DEFAULT_DATA)
T86_DIR = os.environ.get("QLAB_T86_DIR", _DEFAULT_T86)

KLINE_PATH = os.path.join(DATA_DIR, "kline_deep.json")
REVENUE_PATH = os.path.join(DATA_DIR, "revenue.json")

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
