import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RiskConfigContractTests(unittest.TestCase):
    def test_backtest_and_execution_risk_are_explicit_and_aligned(self):
        config = json.loads((ROOT / "config" / "convexity_config.json").read_text(encoding="utf-8"))
        backtest_risk = config["backtest"]["risk_per_trade_pct"]
        execution_risk = config["execution"]["risk_per_trade_pct"]
        self.assertIsInstance(backtest_risk, (int, float))
        self.assertGreater(backtest_risk, 0)
        self.assertEqual(backtest_risk, execution_risk)

    def test_live_safety_flags_are_explicit(self):
        config = json.loads((ROOT / "config" / "convexity_config.json").read_text(encoding="utf-8"))
        execution = config["execution"]
        self.assertIsInstance(execution["shadow_mode"], bool)
        self.assertIsInstance(execution["allow_real_orders"], bool)
        self.assertTrue(execution["shadow_mode"])
        self.assertFalse(execution["allow_real_orders"])

    def test_backtest_banner_uses_configured_risk_not_hardcoded_half_percent(self):
        source = (ROOT / "run_convexity_backtest.py").read_text(encoding="utf-8")
        self.assertIn('config.get("backtest", {}).get("risk_per_trade_pct"', source)
        self.assertNotIn("İşlem Başı Portföyün Tam %0.5'i", source)


if __name__ == "__main__":
    unittest.main()
