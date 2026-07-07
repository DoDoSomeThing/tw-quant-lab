#!/usr/bin/env python3
"""
驗證「驗證器本身」—— 合成宇宙已知答案,框架結論必須一致:

  1. 植入真 edge   → 超額必須顯著為正(框架抓得到真訊號)
  2. 無 edge 亂選   → 超額必須貼近 0(框架不產生幻覺)
  3. 偷看未來(CHEAT)→ 超額必須爆高(如果連作弊都測不出正報酬,引擎壞了);
     同時當 lookahead 上限參考:真訊號分數不該逼近 CHEAT
  4. Context point-in-time:窗口/動能/營收查詢絕不含未來資料
"""
import os
import random
import statistics
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from framework.data import Data
from framework.context import Context
from framework.engine import run
from framework import gates
from tests import synth


def _mean(pairs):
    return statistics.mean(v for _, v in pairs)


class TestPlantedEdge(unittest.TestCase):
    """已知有 edge 的宇宙:框架必須亮綠。"""

    @classmethod
    def setUpClass(cls):
        d, winners = synth.planted_universe()
        cls.path = synth.write_kline(d)
        cls.data = Data(kline_path=cls.path)
        cls.ctx = Context(cls.data)
        cls.winners = winners

    @classmethod
    def tearDownClass(cls):
        synth.cleanup(cls.path)

    def test_detects_planted_edge(self):
        sig = lambda sid, dt, ctx: 1.0 if sid in self.winners else 0.0
        pairs, perdate = run(sig, self.data, self.ctx)
        self.assertGreaterEqual(len(pairs), 8)
        m = _mean(pairs)
        self.assertGreater(m, 0.01, "植入 +0.2%/日 edge,框架卻沒驗出正超額 → 引擎壞了")
        _, lo, _hi, p = gates.bootstrap([v for _, v in pairs])
        self.assertLess(p, 0.05)
        self.assertGreater(lo, 0)

    def test_no_edge_signal_stays_flat(self):
        rng = random.Random(7)
        sig = lambda sid, dt, ctx: rng.random()      # 純亂數分數
        pairs, _ = run(sig, self.data, self.ctx)
        planted = _mean(run(lambda s, d, c: 1.0 if s in self.winners else 0.0,
                            self.data, self.ctx)[0])
        m = _mean(pairs)
        # 亂選 = 貼近等權基準,只剩成本拖累;必須遠小於植入 edge 的超額
        self.assertLess(abs(m), planted / 3,
                        f"亂訊號超額 {m:+.4f} 不該接近植入 edge 的 {planted:+.4f}")


class TestCheatDetector(unittest.TestCase):
    """全隨機宇宙:誠實訊號賺不到;偷看未來的 CHEAT 必須爆高。"""

    @classmethod
    def setUpClass(cls):
        cls.path = synth.write_kline(synth.random_universe())
        cls.data = Data(kline_path=cls.path)
        cls.ctx = Context(cls.data)

    @classmethod
    def tearDownClass(cls):
        synth.cleanup(cls.path)

    def test_cheat_explodes_honest_stays_flat(self):
        def cheat(sid, dt, ctx):        # 偷看 20 日後收盤(訊號函式作弊示範)
            fwd = ctx.shift(dt, 20)
            if not fwd:
                return None
            c0, c1 = ctx.close(sid, dt), ctx.close(sid, fwd)
            return (c1 / c0 - 1) if c0 and c1 else None

        rng = random.Random(11)
        honest = lambda sid, dt, ctx: rng.random()

        cheat_m = _mean(run(cheat, self.data, self.ctx)[0])
        honest_m = _mean(run(honest, self.data, self.ctx)[0])
        self.assertGreater(cheat_m, 0.02, "連偷看未來都測不出正超額 → 引擎的報酬計算壞了")
        self.assertLess(abs(honest_m), cheat_m / 3)
        # 用法備忘:真實 study 若跑出逼近 CHEAT 等級的超額,先懷疑 lookahead,不是慶祝。


class TestPointInTime(unittest.TestCase):
    """Context 絕不偷看未來。"""

    @classmethod
    def setUpClass(cls):
        cls.path = synth.write_kline(synth.random_universe(n_stocks=5, n_days=200))
        cls.data = Data(kline_path=cls.path)

    @classmethod
    def tearDownClass(cls):
        synth.cleanup(cls.path)

    def test_window_excludes_future(self):
        ctx = Context(self.data)
        cal = self.data.cal
        sid = "R00"
        dt = cal[60]
        w = ctx.window(sid, dt, 30)
        # 對照:自己取 <=dt 的收盤,長度與值必須一致
        expect = [self.data.C[sid][d] for d in cal[30:61]]
        self.assertEqual(w, expect)

    def test_momentum_uses_only_past(self):
        ctx = Context(self.data)
        cal = self.data.cal
        dt = cal[80]
        m = ctx.momentum("R01", dt, lookback=40, gap=10)
        p0 = self.data.C["R01"][cal[40]]
        p1 = self.data.C["R01"][cal[70]]
        self.assertAlmostEqual(m, p1 / p0 - 1)

    def test_revenue_respects_avail_date(self):
        cal = self.data.cal
        avail = cal[80]     # 公布日:這天(含)之後才查得到
        before = cal[79]
        rev = {"R02": [[avail, 202207, 200.0], ["2021-08-10", 202107, 100.0]]}
        ctx = Context(self.data, revenue=rev)
        self.assertIsNone(ctx.revenue_yoy("R02", before), "公布日前就查到 = 偷看未來")
        self.assertAlmostEqual(ctx.revenue_yoy("R02", avail), 1.0)
        self.assertAlmostEqual(ctx.revenue_yoy("R02", cal[-1]), 1.0)

    def test_regime_warmup_is_none(self):
        self.assertIsNone(self.data.regime[self.data.cal[0]])
        self.assertIsNotNone(self.data.regime[self.data.cal[-1]])


class TestDataDerived(unittest.TestCase):
    """Data 衍生結構的小型手算對照。"""

    def test_build_ew_hand_computed(self):
        dates = synth.trading_days(n=5)
        d = {
            "0050": synth._bars(dates, [100, 100, 100, 100, 100]),
            "AAA": synth._bars(dates, [100, 110, 121, 133.1, 146.41]),   # +10%/日
            "BBB": synth._bars(dates, [100, 100, 100, 100, 100]),        # 平
        }
        path = synth.write_kline(d)
        try:
            data = Data(kline_path=path)
            ew = data.build_ew(1)
            # 第1天等權 = mean(+10%, 0%) = +5%
            self.assertAlmostEqual(ew[dates[0]], 0.05, places=6)
        finally:
            synth.cleanup(path)


if __name__ == "__main__":
    unittest.main()
