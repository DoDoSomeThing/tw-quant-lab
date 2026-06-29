#!/usr/bin/env python3
"""
範例:看似最有料的訊號 —— 大型 × 高YoY(>20%) × 非空頭 × 站MA60 × 60日動能>0。
只寫一個 signal,validate() 自動跑五關拆穿:
  關1 帳面超額正、關3 贏得過隨機(選的確實比亂選好)——看起來有戲;
  但 關2b 2025 真OOS 轉負、bootstrap 不顯著(p≈0.10)、2025 樣本太少 → 判 🟡 非穩 edge。
這就是重點:帳面贏 ≠ edge,得過完五關。完整 event 版(逐筆營收事件、n 較大、
2025 破更明顯)見 studies/revenue_stress.py;對照即 README case study。
用法: python studies/example_revenue_combo.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from framework.engine import validate


def signal(sid, date, ctx):
    if ctx.avgvol(sid) < ctx.size_pct(0.5):        # 只大型(量前50%)
        return None
    if ctx.regime(date) is not True:               # 只非空頭(0050>MA120)
        return None
    yoy = ctx.revenue_yoy(sid, date)               # point-in-time 月營收 YoY
    if yoy is None or yoy < 0.2:                    # 高 YoY > 20%
        return None
    px = ctx.close(sid, date)
    ma60 = ctx.ma(sid, date, 60)
    mom = ctx.momentum(sid, date, lookback=60)
    if px is None or ma60 is None or mom is None:
        return None
    if px <= ma60 or mom <= 0:                      # 站上 MA60 + 動能>0
        return None
    return yoy                                      # 用 YoY 當分數


if __name__ == "__main__":
    # top_pct=1.0 → 每月等權持有「所有」符合條件的股(貼近 event 研究,n 較大)
    validate(signal, "大型×高YoY×非空頭×MA60×動能", with_revenue=True, top_pct=1.0)
