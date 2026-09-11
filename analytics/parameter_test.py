import os
import sys
import json

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
from pathlib import Path
import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from data.market_loader import MarketLoader
from engine.trend_detector import TrendDetector
from engine.convexity_simulator import ConvexitySimulator
from analytics.performance_reporter import PerformanceReporter

def run_parameter_sensitivity():
    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Clean trend assets only
    chop_pairs = ["USDCAD", "GBPJPY", "EURJPY", "AUDUSD"]
    symbols = [s for s in config.get("backtest", {}).get("symbols", []) if s not in chop_pairs]

    loader = MarketLoader()
    detector = TrendDetector(config)

    # Preload symbol dfs and signals
    data_cache = {}
    for symbol in symbols:
        df = loader.load_symbol_data(symbol, period="5y")
        if df.empty or len(df) < 200:
            continue
        signals = []
        for i in range(1, len(df)):
            sig = detector.check_breakout(df, i, symbol)
            if sig:
                signals.append(sig)
        data_cache[symbol] = (df, signals)

    print("\n" + "═" * 80)
    print(" 🔬 PARAMETRE DUYARLILIK ANALİZİ (CLEAN BASKET - 12 VARLIK) 🔬 ")
    print(f"{'Partial TP':<12} │ {'Trailing ATR':<14} │ {'Win Rate':<10} │ {'Payoff':<8} │ {'Kâr Faktörü':<12} │ {'Net R'}")
    print("─" * 80)

    for p_tp in [1.5, 2.0, 2.5]:
        for t_atr in [1.5, 2.0, 2.5, 3.0]:
            cfg_copy = json.loads(json.dumps(config))
            cfg_copy["mechanics"]["partial_tp_r"] = p_tp
            cfg_copy["mechanics"]["trailing_atr_multiplier"] = t_atr
            sim = ConvexitySimulator(cfg_copy)

            all_t = []
            for symbol, (df, signals) in data_cache.items():
                trades = sim.simulate_trades_for_symbol(df, signals, symbol)
                all_t.extend(trades)

            rep = PerformanceReporter.calculate(all_t)
            print(f"+{p_tp:<4.1f} R      │ {t_atr:<4.1f}x ATR       │ %{rep.win_rate_pct:<7.1f} │ {rep.payoff_ratio:<6.2f}x │ {rep.profit_factor:<10.2f}   │ {rep.total_r:>+7.2f} R")

if __name__ == "__main__":
    run_parameter_sensitivity()
