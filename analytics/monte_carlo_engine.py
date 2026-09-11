import os
import sys
import json
import random
from pathlib import Path
from typing import List, Dict
import numpy as np

# Set UTF-8
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from data.market_loader import MarketLoader
from engine.trend_detector import TrendDetector
from engine.convexity_simulator import ConvexitySimulator, ConvexityTrade
from analytics.performance_reporter import PerformanceReporter

def run_monte_carlo_and_basket_analysis():
    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    symbols = config.get("backtest", {}).get("symbols", [])
    initial_capital = config.get("backtest", {}).get("initial_capital", 10000.0)

    loader = MarketLoader()
    detector = TrendDetector(config)
    simulator = ConvexitySimulator(config)

    all_trades: List[ConvexityTrade] = []
    symbol_trades: Dict[str, List[ConvexityTrade]] = {}

    for symbol in symbols:
        df = loader.load_symbol_data(symbol, period="5y")
        if df.empty or len(df) < 200:
            continue
        signals = []
        for i in range(1, len(df)):
            sig = detector.check_breakout(df, i, symbol)
            if sig:
                signals.append(sig)
        trades = simulator.simulate_trades_for_symbol(df, signals, symbol)
        symbol_trades[symbol] = trades
        all_trades.extend(trades)

    all_trades.sort(key=lambda t: t.entry_date)
    pnl_r_list = [t.pnl_r for t in all_trades]
    total_trades = len(pnl_r_list)

    print("\n" + "═" * 95)
    print(" 🎲 MONTE CARLO SIRALAMA RİSKİ SİMÜLASYONU (1.000 PERMÜTASYON) 🎲 ")
    print(f"    Toplam İşlem Sayısı: {total_trades} | Risk: %0.5 / İşlem")
    print("═" * 95)

    # 1.000 Monte Carlo Permütasyonu
    num_simulations = 1000
    mc_max_dds_r = []
    mc_max_consec_losses = []
    mc_max_dd_pct = []

    np.random.seed(42)

    for _ in range(num_simulations):
        shuffled = np.random.permutation(pnl_r_list)
        
        # Max Drawdown R
        cum = np.cumsum(shuffled)
        peak = np.maximum.accumulate(cum)
        dd = peak - cum
        max_dd = np.max(dd)
        mc_max_dds_r.append(max_dd)
        mc_max_dd_pct.append(max_dd * 0.5) # %0.5 risk

        # Max Consecutive Losses
        consec = 0
        max_consec = 0
        for r in shuffled:
            if r <= 0:
                consec += 1
                if consec > max_consec:
                    max_consec = consec
            else:
                consec = 0
        mc_max_consec_losses.append(max_consec)

    # İstatistikler
    p50_dd_r = np.percentile(mc_max_dds_r, 50)
    p95_dd_r = np.percentile(mc_max_dds_r, 95)
    p99_dd_r = np.percentile(mc_max_dds_r, 99)
    worst_dd_r = np.max(mc_max_dds_r)

    p50_dd_pct = np.percentile(mc_max_dd_pct, 50)
    p95_dd_pct = np.percentile(mc_max_dd_pct, 95)
    p99_dd_pct = np.percentile(mc_max_dd_pct, 99)
    worst_dd_pct = np.max(mc_max_dd_pct)

    p50_consec = np.percentile(mc_max_consec_losses, 50)
    p95_consec = np.percentile(mc_max_consec_losses, 95)
    p99_consec = np.percentile(mc_max_consec_losses, 99)
    worst_consec = np.max(mc_max_consec_losses)

    print(f"{'Metrik':<35} │ {'Medyan (%50)':<15} │ {'%95 Güven Aralığı':<18} │ {'En Kötü Senaryo (Worst-Case)'}")
    print("─" * 95)
    print(f"{'Maksimum Drawdown (R Cinsinden)':<35} │ {p50_dd_r:>6.1f} R        │ {p95_dd_r:>6.1f} R           │ {worst_dd_r:>6.1f} R")
    print(f"{'Portföy Erimesi (%0.5 Risk ile)':<35} │ %{p50_dd_pct:>5.1f}        │ %{p95_dd_pct:>5.1f}           │ %{worst_dd_pct:>5.1f}")
    print(f"{'Ardışık Stop Serisi (Consec Losses)':<35} │ {p50_consec:>6.0f} işlem    │ {p95_consec:>6.0f} işlem      │ {worst_consec:>6.0f} işlem")
    print("═" * 95)

    is_safe = worst_dd_pct <= 25.0
    print(f"\n🛡️ SERMAYE ERİME DEĞERLENDİRMESİ: ")
    print(f"   En Kötü Monte Carlo Portföy Erimesi: %{worst_dd_pct:.1f}")
    if is_safe:
        print(f"   ✅ SONUÇ: Portföy erimesi %25 kritik eşiğinin ALTINDA (GÜVENLİ!). %0.5 risk canlıyı rahatlıkla kaldırır.")
    else:
        print(f"   ⚠️ SONUÇ: En kötü senaryoda erime %{worst_dd_pct:.1f} ile %25 eşiğini aşıyor. Canlıda risk %0.30 - %0.35'e çekilmelidir.")

    # 3. KÂR FAKTÖRÜNÜ ARTIRMA: DÜŞÜK PERFORMANSLI 4 VARLIĞIN ELENMESİ
    print("\n" + "═" * 95)
    print(" 🎯 SEPET TEMİZLİĞİ: MEAN-REVERTING TESTERE VARLIKLARIN ELENMESİ 🎯 ")
    print("═" * 95)

    # Negatif 4 varlık: USDCAD, GBPJPY, EURJPY, AUDUSD
    chop_pairs = ["USDCAD", "GBPJPY", "EURJPY", "AUDUSD"]
    clean_trades = [t for t in all_trades if t.symbol not in chop_pairs]
    clean_trades.sort(key=lambda t: t.entry_date)

    clean_report = PerformanceReporter.calculate(clean_trades, name="Cleaned Trend Basket", initial_capital=initial_capital)
    raw_report = PerformanceReporter.calculate(all_trades, name="Full 16 Assets", initial_capital=initial_capital)

    print(f"{'Metrik':<35} │ {'16 Varlık (Tüm Sepet)':<24} │ {'12 Varlık (Temizlenmiş Sepet)'}")
    print("─" * 95)
    print(f"{'Toplam İşlem ($N$)':<35} │ {raw_report.total_trades:>6} işlem             │ {clean_report.total_trades:>6} işlem")
    print(f"{'Kazanma Oranı (Win Rate)':<35} │ %{raw_report.win_rate_pct:>5.1f}                   │ %{clean_report.win_rate_pct:>5.1f}")
    print(f"{'Kâr Faktörü (Profit Factor)':<35} │ {raw_report.profit_factor:>6.2f}                   │ {clean_report.profit_factor:>6.2f}  🚀")
    print(f"{'Matematiksel Beklenti (E)':<35} │ {raw_report.expectancy_r:>+6.2f} R / işlem          │ {clean_report.expectancy_r:>+6.2f} R / işlem")
    print(f"{'Toplam Net Getiri (R)':<35} │ {raw_report.total_r:>+6.2f} R                 │ {clean_report.total_r:>+6.2f} R")
    print(f"{'Komisyon Sonrası Net R':<35} │ {raw_report.total_r - raw_report.total_trades*0.05:>+6.2f} R                 │ {clean_report.total_r - clean_report.total_trades*0.05:>+6.2f} R")
    print(f"{'Maksimum Drawdown':<35} │ -{raw_report.max_drawdown_r:.1f} R (%{raw_report.max_drawdown_pct:.1f})        │ -{clean_report.max_drawdown_r:.1f} R (%{clean_report.max_drawdown_pct:.1f})")
    print("═" * 95)

    # Clean Basket Monte Carlo
    clean_pnl = [t.pnl_r for t in clean_trades]
    clean_mc_dds = []
    clean_mc_consec = []
    for _ in range(num_simulations):
        shuffled = np.random.permutation(clean_pnl)
        cum = np.cumsum(shuffled)
        peak = np.maximum.accumulate(cum)
        dd = peak - cum
        clean_mc_dds.append(np.max(dd) * 0.5)
        
        c = 0
        mc_c = 0
        for r in shuffled:
            if r <= 0:
                c += 1
                if c > mc_c:
                    mc_c = c
            else:
                c = 0
        clean_mc_consec.append(mc_c)

    print(f"\n✨ TEMİZLENMİŞ SEPETTE MONTE CARLO ÇEKİLME: Medyan: %{np.percentile(clean_mc_dds, 50):.1f} │ %95 Güven: %{np.percentile(clean_mc_dds, 95):.1f} │ En Kötü: %{np.max(clean_mc_dds):.1f}")
    print(f"✨ TEMİZLENMİŞ SEPETTE ARDIŞIK STOP: Medyan: {np.percentile(clean_mc_consec, 50):.0f} işlem │ %95 Güven: {np.percentile(clean_mc_consec, 95):.0f} işlem │ En Kötü: {np.max(clean_mc_consec):.0f} işlem")

if __name__ == "__main__":
    run_monte_carlo_and_basket_analysis()
