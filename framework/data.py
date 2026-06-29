#!/usr/bin/env python3
"""
資料載入 + 共用衍生結構。把散在各腳本裡重複的
  load kline / 建 cal / regime MA120 / build_ew / avgvol / next_td
全部收斂到 Data 一個物件,study 只管訊號邏輯。
"""
import json
import bisect
import statistics
import glob
import os

from framework import config


def yr(dt):
    """'2024-03-05' -> '2024'"""
    return dt[:4]


class Data:
    """
    一次載入,衍生全部共用結構。

    屬性:
      d       原始 {sid: [bar,...]},bar={date,open,close,max,min,volume}
      cal     主日曆(預設 = BENCH 的日期序),已排序
      cidx    {date: index in cal}
      C       {sid: {date: close}}
      V       {sid: {date: volume}}
      bc      [BENCH 每日收盤]
      regime  {date: True/False/None}  站上 MA120=True,暖身期=None(不誤判空頭)
      avgvol  {sid: 平均成交量}  (大小代理,排除 BENCH)
    """

    def __init__(self, kline_path=None, bench=None):
        self.bench = bench or config.BENCH
        path = kline_path or config.KLINE_PATH
        if not os.path.exists(path):
            config.require_data()
        self.d = json.load(open(path, encoding="utf-8"))

        if self.bench not in self.d:
            raise SystemExit(f"基準 {self.bench} 不在資料裡。")

        self.cal = [b["date"] for b in self.d[self.bench]]
        self.cidx = {dt: i for i, dt in enumerate(self.cal)}
        self.bc = [b["close"] for b in self.d[self.bench]]

        self.C = {t: {b["date"]: b["close"] for b in bars} for t, bars in self.d.items()}
        self.V = {t: {b["date"]: b.get("volume") for b in bars} for t, bars in self.d.items()}

        # regime:站上 MA120=非空頭。暖身期(<MA)=None,避免把開頭誤判成空頭。
        ma = config.REGIME_MA
        self.regime = {}
        for i, dt in enumerate(self.cal):
            self.regime[dt] = (self.bc[i] > sum(self.bc[i - ma:i]) / ma) if i >= ma else None

        # 平均成交量(大小代理),排除基準
        self.avgvol = {}
        for t, bars in self.d.items():
            if t == self.bench:
                continue
            vs = [b["volume"] for b in bars if b.get("volume")]
            if vs:
                self.avgvol[t] = statistics.mean(vs)

        self._ew_cache = {}

    # ---------- 共用工具 ----------
    def next_td(self, dt):
        """dt(含)起第一個交易日;超出範圍回 None。"""
        i = bisect.bisect_left(self.cal, dt)
        return self.cal[i] if i < len(self.cal) else None

    def build_ew(self, h):
        """
        全市場等權「持有 h 日」前向報酬。{進場日: 全市場(非基準)平均 h 日報酬}。
        這就是第一關的公平基準 —— 不靠台積電權值撐起來的 0050。
        """
        if h in self._ew_cache:
            return self._ew_cache[h]
        ew = {}
        cal, C, bench = self.cal, self.C, self.bench
        for i in range(len(cal) - h):
            e, x = cal[i], cal[i + h]
            rs = []
            for t in C:
                if t == bench:
                    continue
                ce = C[t].get(e)
                cx = C[t].get(x)
                if ce and cx and ce > 0:
                    rs.append(cx / ce - 1)
            if rs:
                ew[e] = statistics.mean(rs)
        self._ew_cache[h] = ew
        return ew

    def bench_fwd(self, e, x):
        """基準(0050)從 e 到 x 的報酬;任一缺值回 None。"""
        be = self.C[self.bench].get(e)
        bx = self.C[self.bench].get(x)
        if be and bx and be > 0:
            return bx / be - 1
        return None

    def size_pct(self, p):
        """成交量分位門檻(p=0.5 → 中位數)。回該分位的 avgvol 值。"""
        vols = sorted(self.avgvol.values())
        if not vols:
            return 0
        return vols[min(int(p * len(vols)), len(vols) - 1)]


def load_revenue(path=None):
    """月營收 {sid: [[avail_date, yyyymm, rev], ...]}。"""
    path = path or config.REVENUE_PATH
    if not os.path.exists(path):
        config.require_data()
    return json.load(open(path, encoding="utf-8"))


def load_t86(t86_dir=None):
    """
    法人買賣超,逐日 json(檔名 YYYYMMDD.json,內容 {code:[外資,投信,自營]})。
    回 {sid: {date(YYYY-MM-DD): (投信, 外資)}}。沒資料夾回空 dict。
    """
    t86_dir = t86_dir or config.T86_DIR
    inst = {}
    for f in glob.glob(os.path.join(t86_dir, "*.json")):
        base = os.path.basename(f)[:8]
        dt = f"{base[:4]}-{base[4:6]}-{base[6:]}"
        try:
            j = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(j, dict):
            continue
        for sid, vals in j.items():
            if isinstance(vals, (list, tuple)) and len(vals) >= 2:
                inst.setdefault(str(sid), {})[dt] = (vals[1], vals[0])  # (投信, 外資)
    return inst
