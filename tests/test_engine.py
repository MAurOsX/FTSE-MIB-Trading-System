import unittest
import numpy as np
import pandas as pd

from engine import add_indicators, load_constituents, run_backtest, BENCHMARK


class EngineSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        n = 300
        idx = pd.bdate_range("2024-01-01", periods=n)
        close = pd.Series(100 * np.exp(np.cumsum(np.random.default_rng(7).normal(0, 0.008, n))), index=idx)
        cls.data = pd.DataFrame({
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": np.random.default_rng(8).integers(1000, 5000, n),
        }, index=idx)

    def test_constituents_are_40_and_unique(self):
        df = load_constituents()
        self.assertEqual(len(df), 40)
        self.assertEqual(df["ticker"].nunique(), 40)

    def test_benchmark_symbol(self):
        self.assertEqual(BENCHMARK, "FTSEMIB.MI")

    def test_indicators(self):
        out = add_indicators(self.data, self.data)
        for col in ["PSAR", "SMA20", "SMA50", "SMA200", "VolumeRatio", "Correlation60D"]:
            self.assertIn(col, out.columns)
        self.assertFalse(out["SMA200"].dropna().empty)

    def test_backtest(self):
        curve, stats = run_backtest(self.data)
        self.assertEqual(len(curve), len(self.data))
        self.assertIn("CAGR", stats)
        self.assertIn("Max drawdown", stats)


if __name__ == "__main__":
    unittest.main()
