#!/usr/bin/env python3
"""
範例:12-1 月動能(通用訊號介面 demo)。
只寫一個 signal 函式,validate() 自動跑五關。
用法: python studies/example_momentum.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from framework.engine import validate


def signal(sid, date, ctx):
    """12-1 動能:過去 12 個月漲幅(跳過最近 1 個月)。越大越優先。"""
    return ctx.momentum(sid, date, lookback=252, gap=21)


if __name__ == "__main__":
    # 動能不需營收/法人 → 關掉以加速
    validate(signal, "12-1 月動能", with_revenue=False)
