#!/usr/bin/env python3
"""gates.py 純函式單元測試(不需資料檔)。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from framework import gates


class TestStats(unittest.TestCase):
    def test_basic(self):
        n, mean, pos = gates.stats([0.01, -0.01, 0.03, 0.01])
        self.assertEqual(n, 4)
        self.assertAlmostEqual(mean, 0.01)
        self.assertAlmostEqual(pos, 75.0)

    def test_empty(self):
        self.assertEqual(gates.stats([]), (0, 0.0, 0.0))


class TestBootstrap(unittest.TestCase):
    def test_clear_positive(self):
        xs = [0.02, 0.03, 0.025, 0.018, 0.022, 0.028, 0.019, 0.031] * 5
        m, lo, hi, p = gates.bootstrap(xs)
        self.assertGreater(m, 0)
        self.assertGreater(lo, 0)      # CI 全在 0 之上
        self.assertLess(p, 0.05)       # 顯著

    def test_noise_not_significant(self):
        xs = [0.01, -0.01] * 20        # 均值恰為 0
        m, lo, hi, p = gates.bootstrap(xs)
        self.assertAlmostEqual(m, 0.0, places=6)
        self.assertGreater(p, 0.05)

    def test_deterministic_and_order_free(self):
        """同輸入永遠同輸出;先跑別的 bootstrap 也不影響(local seed 的意義)。"""
        xs = [0.01, 0.02, -0.005, 0.015, 0.03, -0.01, 0.02, 0.005]
        a = gates.bootstrap(xs)
        gates.bootstrap([0.5, -0.5] * 10)   # 中間插一次別的呼叫
        b = gates.bootstrap(xs)
        self.assertEqual(a, b)

    def test_empty(self):
        self.assertIsNone(gates.bootstrap([]))


class TestVerdict(unittest.TestCase):
    def test_negative_mean(self):
        self.assertIn("負期望", gates.verdict(0.01, -0.001))

    def test_significant_positive(self):
        self.assertIn("顯著", gates.verdict(0.01, 0.02))

    def test_not_significant(self):
        self.assertIn("運氣", gates.verdict(0.5, 0.02))


class TestRandomControl(unittest.TestCase):
    def test_real_edge_beats_random(self):
        real = [0.05] * 24                       # 明顯正
        draw = lambda: [0.0] * 24                # 隨機組永遠 0
        better = gates.random_control(real, draw, trials=50)
        self.assertEqual(better, 0.0)            # 隨機從沒贏過

    def test_no_edge_loses_to_random(self):
        real = [0.0] * 24
        draw = lambda: [0.01] * 24               # 隨機組更好
        better = gates.random_control(real, draw, trials=50)
        self.assertEqual(better, 1.0)


if __name__ == "__main__":
    unittest.main()
