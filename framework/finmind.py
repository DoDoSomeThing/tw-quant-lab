#!/usr/bin/env python3
"""
精簡資料抓取工具(自帶,零外部專案依賴)。
backfill/ 用這支拿 logger / 帶重試的 http_get / 上市股票清單。
FinMind token 一律走環境變數 FINMIND_TOKEN(不寫死)。
"""
import os
import logging
import time

import requests

FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"


def get_logger(name="qlab"):
    lg = logging.getLogger(name)
    if not lg.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        lg.addHandler(h)
        lg.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
        lg.propagate = False
    return lg


_log = get_logger(__name__)
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "tw-quant-lab/1.0"})


def http_get(url, *, timeout=15, retries=3, backoff=3, **kwargs):
    """GET with 簡單重試。kwargs 透傳 requests(headers/params/...)。"""
    last = None
    for attempt in range(retries):
        try:
            return _SESSION.get(url, timeout=timeout, **kwargs)
        except Exception as e:
            last = e
            time.sleep(backoff * (attempt + 1))
    raise last


def get_tse_stock_list():
    """
    上市股票清單 {code: name}。
    兩個來源合併 —— t187ap03_L 是**公司**基本資料,**不含 ETF**(0050/0056 等會漏掉,
    基準抓不到就整個回測跑不起來),所以再用 STOCK_DAY_ALL(當日全部交易標的)補齊。
    """
    stocks = {}
    try:
        for s in http_get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", timeout=15).json():
            code = (s.get("公司代號") or "").strip()
            name = (s.get("公司簡稱") or "").strip()
            if code and name:
                stocks[code] = name
    except Exception as e:
        _log.warning(f"抓公司基本資料失敗:{e}")
    try:
        for s in http_get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                          timeout=15).json():
            code = (s.get("Code") or "").strip()
            name = (s.get("Name") or "").strip()
            if code and name:
                stocks.setdefault(code, name)
    except Exception as e:
        _log.warning(f"抓當日交易清單失敗:{e}")
    return stocks
