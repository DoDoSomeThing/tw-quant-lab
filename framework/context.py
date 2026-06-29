#!/usr/bin/env python3
"""
Context —— 傳給使用者訊號函式的查詢物件。

通用訊號介面:
    def signal(sid, date, ctx) -> float | None:
        # 回傳該股當日訊號分數(None = 不選);分數越大越優先(可設 pick_top=False 反向)
        ...

ctx 提供「**到 date 為止**」的 point-in-time 查詢(不偷看未來):
  價量    ctx.close / ctx.window / ctx.momentum / ctx.ma / ctx.volatility
  日曆    ctx.shift / ctx.regime / ctx.avgvol / ctx.size_pct
  營收    ctx.revenue_yoy(最新一筆 avail_date <= date 的 YoY)
  法人    ctx.inst_buy(前一交易日投信或外資淨買)
"""
import bisect
import statistics


class Context:
    def __init__(self, data, revenue=None, inst=None):
        self.D = data
        self.cal = data.cal
        self.cidx = data.cidx
        self._inst = inst or {}

        # 營收:每檔預算 (avail_date, yoy) 排序序列,查詢時 bisect 取最新一筆 <= date
        self._yoy = {}
        if revenue:
            for sid, rows in revenue.items():
                by_ym = {r[1]: r[2] for r in rows if r[2]}
                seq = []
                for avail, ym, r in sorted(rows, key=lambda x: x[0]):
                    if not r:
                        continue
                    prev = by_ym.get(ym - 100)
                    if not prev or prev <= 0:
                        continue
                    seq.append((avail, r / prev - 1))
                if seq:
                    self._yoy[sid] = seq

    # ---- 價量(point-in-time)----
    def close(self, sid, date):
        return self.D.C.get(sid, {}).get(date)

    def shift(self, date, n):
        """date 後 n 個交易日(n 可負);超界回 None。"""
        i = self.cidx.get(date)
        if i is None:
            return None
        j = i + n
        return self.cal[j] if 0 <= j < len(self.cal) else None

    def window(self, sid, date, lookback):
        """[date-lookback, date] 區間收盤(含 date),只回有值的;不偷看未來。"""
        i = self.cidx.get(date)
        if i is None:
            return []
        C = self.D.C.get(sid, {})
        out = []
        for k in range(max(0, i - lookback), i + 1):
            v = C.get(self.cal[k])
            if v and v > 0:
                out.append(v)
        return out

    def momentum(self, sid, date, lookback, gap=0):
        """(date-gap) / (date-lookback) - 1。經典 12-1 動能用 lookback=252, gap=21。"""
        d0 = self.shift(date, -lookback)
        d1 = self.shift(date, -gap)
        if not d0 or not d1:
            return None
        p0 = self.close(sid, d0)
        p1 = self.close(sid, d1)
        if p0 and p1 and p0 > 0:
            return p1 / p0 - 1
        return None

    def ma(self, sid, date, n):
        w = self.window(sid, date, n)
        return statistics.mean(w) if len(w) >= n * 0.8 else None

    def volatility(self, sid, date, n=60):
        w = self.window(sid, date, n)
        if len(w) < n * 0.8:
            return None
        rets = [w[j] / w[j - 1] - 1 for j in range(1, len(w))]
        return statistics.pstdev(rets) if len(rets) > 1 else None

    # ---- 日曆 / 市況 / 規模 ----
    def regime(self, date):
        """站上 MA120=True、空頭=False、暖身=None。"""
        return self.D.regime.get(date)

    def avgvol(self, sid):
        return self.D.avgvol.get(sid, 0)

    def size_pct(self, p):
        return self.D.size_pct(p)

    # ---- 營收(point-in-time)----
    def revenue_yoy(self, sid, date):
        """最新一筆 avail_date <= date 的月營收 YoY;沒有回 None。"""
        seq = self._yoy.get(sid)
        if not seq:
            return None
        i = bisect.bisect_right([a for a, _ in seq], date) - 1
        return seq[i][1] if i >= 0 else None

    # ---- 法人 ----
    def inst_buy(self, sid, date):
        """前一交易日 投信 or 外資 淨買 > 0。"""
        i = self.cidx.get(date)
        if i is None or i == 0:
            return False
        v = self._inst.get(sid, {}).get(self.cal[i - 1])
        return bool(v and (v[0] > 0 or v[1] > 0))
