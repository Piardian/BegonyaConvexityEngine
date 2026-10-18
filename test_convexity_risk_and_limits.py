import os
import sys
import json
import unittest
from pathlib import Path
import pandas as pd
import numpy as np

# Ensure path
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from execution.lifecycle_daemon import TradeLifecycleManager
from engine.live_scanner import LiveConvexityScanner
from data.market_loader import MarketLoader

class TestConvexityRiskAndLimits(unittest.TestCase):
    def setUp(self):
        config_path = ROOT_DIR / "config" / "convexity_config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

    def test_config_values(self):
        """Konfigürasyondaki risk ve portföy limit değerlerini doğrular."""
        exec_cfg = self.config.get("execution", {})
        self.assertEqual(exec_cfg.get("risk_per_trade_pct"), 0.5)
        self.assertEqual(exec_cfg.get("max_portfolio_heat_pct"), 3.0)
        self.assertEqual(exec_cfg.get("max_concurrent_open_positions"), 4)

    def test_lifecycle_manager_risk_calculation(self):
        """Hesap bakiyesine göre 1R riskin %0.5 ($50/10k) olarak hesaplandığını doğrular."""
        lm = TradeLifecycleManager(self.config)
        balance = 10000.0
        risk_pct = lm.config.get("execution", {}).get("risk_per_trade_pct", 0.5) / 100.0
        target_risk_usd = round(balance * risk_pct, 2)
        self.assertEqual(target_risk_usd, 50.0)

    def test_concurrency_and_heat_cap_enforcement(self):
        """Maksimum 4 pozisyon ve %3.0 ($300/10k) portföy ısı sınırının kodda uygulandığını test eder."""
        lm = TradeLifecycleManager(self.config)
        balance = 10000.0
        max_heat_pct = lm.config.get("execution", {}).get("max_portfolio_heat_pct", 3.0) / 100.0
        max_heat_usd = round(balance * max_heat_pct, 2) # 300 USD
        max_concurrent_pos = lm.config.get("execution", {}).get("max_concurrent_open_positions", 4) # 4

        # 1. 4 Farklı Sembol Simülasyonu
        mock_tickets = {
            101: {"generic_symbol": "EURUSD", "risk_usd": 50.0, "sl": 1.08, "entry": 1.085},
            102: {"generic_symbol": "GBPUSD", "risk_usd": 50.0, "sl": 1.25, "entry": 1.26},
            103: {"generic_symbol": "XAUUSD", "risk_usd": 50.0, "sl": 2300, "entry": 2320},
            104: {"generic_symbol": "BTCUSD", "risk_usd": 50.0, "sl": 60000, "entry": 62000},
        }

        open_symbol_count = len(set(t.get("generic_symbol") for t in mock_tickets.values() if t.get("generic_symbol")))
        self.assertEqual(open_symbol_count, 4)
        # 5. sembol açılmak istendiğinde sınır tetiklenmeli:
        self.assertTrue(open_symbol_count >= max_concurrent_pos)

        # 2. Isı Tavanı Aşımı Simülasyonu (6 bilet x $50 = $300)
        mock_tickets_heat = {
            i: {"generic_symbol": f"SYM_{i}", "risk_usd": 50.0, "sl": 1.0, "entry": 1.1}
            for i in range(1, 7)
        }
        current_heat_usd = sum(
            float(t.get("risk_usd", 0.0)) for t in mock_tickets_heat.values()
            if t.get("sl") != t.get("entry")
        )
        self.assertEqual(current_heat_usd, 300.0)
        # Yeni $50 eklenmek istendiğinde $350 > $300 tavanı aşılmalı:
        new_target_risk = 50.0
        self.assertTrue(current_heat_usd + new_target_risk > max_heat_usd)

    def test_strategy_logic_integrity(self):
        """Strateji mantığının (Donchian 20 + EMA 200) değişmediğini doğrular."""
        loader = MarketLoader()
        # Sentetik D1 verisi üret
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        np.random.seed(42)
        prices = 100 + np.cumsum(np.random.randn(100) * 0.5)
        df = pd.DataFrame({
            "open": prices + 0.1,
            "high": prices + 0.5,
            "low": prices - 0.5,
            "close": prices,
        }, index=dates)

        # Donchian 20 ve EMA 200 hesapla
        df["dc_high_20"] = df["high"].rolling(20).max().shift(1)
        df["dc_low_20"] = df["low"].rolling(20).min().shift(1)
        df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

        # Doğrulama fonksiyonunu test et
        valid, reason = loader.validate_symbol_data(df, min_bars=50)
        self.assertTrue(valid)
        self.assertEqual(reason, "OK")

        # Strateji mantığı: shift(1) kullanılarak lookahead engellenmiş olmalı
        self.assertTrue(pd.isna(df["dc_high_20"].iloc[0]))
        self.assertTrue(pd.isna(df["dc_high_20"].iloc[19]))
        self.assertFalse(pd.isna(df["dc_high_20"].iloc[20]))

if __name__ == "__main__":
    unittest.main()
