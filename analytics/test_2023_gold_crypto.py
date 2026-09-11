import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict
import pandas as pd
import numpy as np

# Windows UTF-8 desteği
if hasattr(sys.stdout, "reconfigure"):
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
from analytics.performance_reporter import PerformanceReporter, ConvexityReport
from analytics.benchmark_collector import BenchmarkCollector

logging.basicConfig(level=logging.WARNING)

def run_2023_gold_crypto_backtest():
    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    symbols = ["XAUUSD", "BTCUSD", "ETHUSD", "SOLUSD"]
    initial_capital = config.get("backtest", {}).get("initial_capital", 10000.0)

    loader = MarketLoader()
    detector = TrendDetector(config)
    simulator = ConvexitySimulator(config)

    all_2023_trades: List[ConvexityTrade] = []
    symbol_reports: List[ConvexityReport] = []

    for symbol in symbols:
        df = loader.load_symbol_data(symbol, period="5y")
        if df.empty or len(df) < 200:
            continue

        signals = []
        for i in range(1, len(df)):
            sig = detector.check_breakout(df, i, symbol)
            if sig:
                signals.append(sig)

        full_trades = simulator.simulate_trades_for_symbol(df, signals, symbol)

        # SADECE 2023 yılında açılan işlemler
        trades_2023 = [t for t in full_trades if "2023-01-01" <= t.entry_date <= "2023-12-31"]
        all_2023_trades.extend(trades_2023)

        rep = PerformanceReporter.calculate(trades_2023, name=symbol, initial_capital=initial_capital)
        symbol_reports.append(rep)

    if not all_2023_trades:
        print("❌ 2023 yılında Altın ve Kripto sepetinde işlem oluşmadı.")
        return

    all_2023_trades.sort(key=lambda t: t.entry_date)
    portfolio_report = PerformanceReporter.calculate(
        all_2023_trades,
        name="2023 Gold & Crypto Portfolio",
        initial_capital=initial_capital
    )

    sep = "═" * 105
    mid = "─" * 105

    print("\n" + sep)
    print(" 🌟 BEGONYA CONVEXITY │ 2023 YILI ALTIN & KRİPTO ÖZEL BACKTEST KARNESİ 🌟 ")
    print("   • Evren              : Altın (XAUUSD) & Kriptolar (BTCUSD, ETHUSD, SOLUSD)")
    print("   • Dönem              : 01 Ocak 2023 - 31 Aralık 2023 (1 Tam Yıl)")
    print(f"   • Risk Yönetimi      : Sabit %0.50 Risk / İşlem ($10,000 Hesapta $50.00)")
    print("   • Strateji Mekaniği  : 20G Donchian + 200 EMA + %50 Kâr Kilit @ 2.0R + 2.5x ATR Trailing")
    print(sep + "\n")

    # 1. Büyük Sonuç
    print("📊 [BÖLÜM 1] 2023 PORTFÖY GENELİ BÜYÜK SONUÇ")
    print(mid)
    w = 38
    print(f"{'Toplam Açılan İşlem (N)':<{w}} : {portfolio_report.total_trades} İşlem")
    print(f"{'Kazanan / Kaybeden':<{w}} : {portfolio_report.wins} Win / {portfolio_report.losses} Loss")
    print(f"{'Kazanma Oranı (Win Rate)':<{w}} : %{portfolio_report.win_rate_pct:.1f}")
    print(f"{'Ortalama Kazanç (Avg Win)':<{w}} : +{portfolio_report.avg_win_r:.2f} R (+${portfolio_report.avg_win_r * 50:.2f})")
    print(f"{'Ortalama Kayıp (Avg Loss)':<{w}} : -{portfolio_report.avg_loss_r:.2f} R (-${portfolio_report.avg_loss_r * 50:.2f})")
    print(f"{'Asimetrik Oran (Payoff Ratio)':<{w}} : {portfolio_report.payoff_ratio:.2f}x")
    print(f"{'İşlem Başı Matematiksel Beklenti':<{w}} : {portfolio_report.expectancy_r:>+5.2f} R / işlem (+${portfolio_report.expectancy_r * 50:.2f})")
    print(f"{'Kâr Faktörü (Profit Factor)':<{w}} : {portfolio_report.profit_factor:.2f}")
    print(mid)
    print(f"{'Toplam Net Getiri (Net R)':<{w}} : {portfolio_report.total_r:>+6.2f} R")
    print(f"{'Sermaye Büyümesi (%0.5 Risk ile)':<{w}} : %{portfolio_report.return_on_capital_pct:>+6.2f} (${portfolio_report.total_pnl_dollars:>+8.2f} Net Kâr)")
    print(f"{'Maksimum Sermaye Çekilmesi (Max DD)':<{w}} : -{portfolio_report.max_drawdown_r:.2f} R (%{portfolio_report.max_drawdown_pct:.2f})")
    print(f"{'En Uzun Art Arda Kayıp Serisi':<{w}} : {portfolio_report.max_consecutive_losses} İşlem")
    print(f"{'Ortalama Pozisyon Taşıma Süresi':<{w}} : {portfolio_report.avg_holding_days:.1f} Gün")
    print(sep + "\n")

    # 2. Varlık Bazlı Tablo
    print("📋 [BÖLÜM 2] ALTIN VE KRİPTOLARIN TEK TEK 2023 KARNESİ")
    print(sep)
    print(f"{'Varlık':<10} │ {'İşlem':<6} │ {'Win %':<8} │ {'Ort.Kâr/Zarar':<15} │ {'Payoff':<7} │ {'Net Getiri (R)':<16} │ {'Net PnL ($)':<12} │ {'Kâr Fakt':<9} │ {'Max DD'}")
    print(mid)
    symbol_reports.sort(key=lambda r: r.total_r, reverse=True)
    for r in symbol_reports:
        win_loss_str = f"+{r.avg_win_r:.1f}R / -{r.avg_loss_r:.1f}R"
        pnl_usd_str = f"${r.total_pnl_dollars:>+8.2f}"
        print(f"{r.name:<10} │ {r.total_trades:>4}   │ %{r.win_rate_pct:>5.1f}  │ {win_loss_str:<15} │ {r.payoff_ratio:>4.1f}x │ {r.total_r:>+7.2f} R          │ {pnl_usd_str:<12} │ {r.profit_factor:>6.2f}   │ -{r.max_drawdown_r:.1f} R")
    print(sep + "\n")

    # 3. Aylık Performans
    print("📅 [BÖLÜM 3] 2023 AYLIK GETİRİ DAĞILIMI (OCAK - ARALIK 2023)")
    print(mid)
    print(f"{'Ay':<14} │ {'Kapanan İşlem':<15} │ {'Win %':<10} │ {'Net Getiri (R)':<16} │ {'Net Getiri ($)':<16} │ {'Kâr Faktörü'}")
    print(mid)
    for m in range(1, 13):
        m_str = f"2023-{m:02d}"
        m_label = f"{m_str}"
        m_trades = [t for t in all_2023_trades if t.exit_date.startswith(m_str)]
        if m_trades:
            m_rep = PerformanceReporter.calculate(m_trades, name=m_label, initial_capital=initial_capital)
            print(f"{m_label:<14} │ {m_rep.total_trades:>8}        │ %{m_rep.win_rate_pct:>5.1f}    │ {m_rep.total_r:>+7.2f} R          │ ${m_rep.total_pnl_dollars:>+9.2f}        │ {m_rep.profit_factor:>6.2f}")
        else:
            print(f"{m_label:<14} │ {'0':>8}        │ %  0.0    │   +0.00 R          │ $    +0.00        │   0.00")
    print(sep + "\n")

    # 4. En Büyük Koşucular (Top Runners)
    print("🚀 [BÖLÜM 4] 2023 YILININ EN BÜYÜK ASİMETRİK İŞLEMLERİ (TOP RUNNERS)")
    print(mid)
    print(f"{'Giriş':<11} {'Çıkış':<11} {'Varlık':<8} {'Yön':<6} {'Giriş':<11} {'Çıkış':<11} {'Süre(G)':<8} {'Zirve R':<8} {'Net R':<9} {'Net PnL ($)'}")
    print(mid)
    top_winners = sorted(all_2023_trades, key=lambda t: t.pnl_r, reverse=True)[:8]
    for w in top_winners:
        print(f"{w.entry_date:<11} {w.exit_date:<11} {w.symbol:<8} {w.direction:<6} {w.entry_price:<11.2f} {w.exit_price:<11.2f} {w.holding_days:<8} {w.peak_r:>+5.1f}R  {w.pnl_r:>+6.2f}R  +${w.pnl_dollars:.2f}")
    print(sep + "\n")

    # 5. Kümülatif Getiri Eğrisi
    print("📈 [BÖLÜM 5] 2023 ALTIN & KRİPTO BİLEŞİK GETİRİ EĞRİSİ (EQUITY CURVE)")
    print(PerformanceReporter.render_ascii_curve(all_2023_trades, height=10, width=60))
    print(sep + "\n")

    # 6. Benchmark Kaydı
    collector = BenchmarkCollector()
    saved_path = collector.log_backtest_benchmark(
        portfolio_report=portfolio_report,
        config={**config, "symbols": symbols},
        symbol_reports=symbol_reports,
        notes="2023 Altın & Kripto Özel Yıllık Backtesti (XAUUSD, BTC, ETH, SOL)"
    )
    print(f"📁 2023 Benchmark kaydı arşivlendi: {saved_path}\n")

if __name__ == "__main__":
    run_2023_gold_crypto_backtest()
