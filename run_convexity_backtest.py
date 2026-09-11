import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict
import pandas as pd

# Windows UTF-8 desteği
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from data.market_loader import MarketLoader
from engine.trend_detector import TrendDetector
from engine.convexity_simulator import ConvexitySimulator, ConvexityTrade
from analytics.performance_reporter import PerformanceReporter
from analytics.benchmark_collector import BenchmarkCollector

logging.basicConfig(level=logging.WARNING)

def load_config() -> dict:
    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)

def run_convexity_backtest():
    config = load_config()
    symbols = config.get("backtest", {}).get("symbols", [])
    initial_capital = config.get("backtest", {}).get("initial_capital", 10000.0)

    loader = MarketLoader()
    detector = TrendDetector(config)
    simulator = ConvexitySimulator(config)

    all_portfolio_trades: List[ConvexityTrade] = []
    symbol_reports = []

    print("\n" + "═" * 106)
    print(" 🏛️ ASİMETRİK RİSK & D1/W1 TREND TAKİPÇİLİĞİ (CONVEXITY ENGINE) 5 YILLIK TESTİ 🏛️ ")
    print("   • Zaman Dilimi     : Günlük (D1) / 20 Günlük Donchian Zirve & Dip Kırılımları")
    print("   • Risk Yönetimi    : İşlem Başı Portföyün Tam %0.5'i (Sabit Risk)")
    print("   • Volatilite Stopu : 1.5x ATR(14) (Piyasa Gürültüsüne Karşı Geniş Stop)")
    print("   • Kâr Simetrisi    : +1.5R'da Breakeven | 2.0x ATR Trailing Stop ile Kârı Koştur (Sabit TP Yok)")
    print("   • Test Evreni      : 16 Çoklu Varlık (FX, JPY Çaprazları, Altın, Petrol, Gümüş, Nasdaq, S&P, Kripto)")
    print("   • Dönem            : 2021 - 2026 (5 Tam Yıl)")
    print("═" * 106 + "\n")

    for symbol in symbols:
        df = loader.load_symbol_data(symbol, period="5y")
        if df.empty or len(df) < 200:
            print(f"  ⚠️ [{symbol:<8}]: Yeterli veri bulunamadı, atlanıyor.")
            continue

        signals = []
        for i in range(1, len(df)):
            sig = detector.check_breakout(df, i, symbol)
            if sig:
                signals.append(sig)

        trades = simulator.simulate_trades_for_symbol(df, signals, symbol)
        all_portfolio_trades.extend(trades)

        rep = PerformanceReporter.calculate(trades, name=symbol, initial_capital=initial_capital)
        symbol_reports.append(rep)

        print(f"  • {symbol:<8}: {rep.total_trades:>2} işlem │ Win Rate: %{rep.win_rate_pct:>4.1f} │ Payoff: {rep.payoff_ratio:>4.1f}x │ Net R: {rep.total_r:>+6.2f} R │ Max DD: -{rep.max_drawdown_r:>4.1f}R │ Kâr Fakt: {rep.profit_factor:>4.2f}")

    if not all_portfolio_trades:
        print("❌ Hiçbir sembolde işlem oluşmadı.")
        return

    # Tarihe göre sırala
    all_portfolio_trades.sort(key=lambda t: t.entry_date)
    portfolio_report = PerformanceReporter.calculate(all_portfolio_trades, name="Unified Convexity Portfolio", initial_capital=initial_capital)

    # 1. Parite Bazında Karne Tablosu
    sep = "═" * 106
    mid = "─" * 106
    print("\n" + sep)
    print("  📊 16 ENSTRÜMAN BAZINDA TEK TEK 5 YILLIK ASİMETRİK RİSK PERFORMANS KARNESİ 📊  ")
    print(sep)
    print(f"{'Varlık':<9} │ {'İşlem ($N$)':<12} │ {'Win Rate':<10} │ {'Ort.Win/Loss':<14} │ {'Payoff':<8} │ {'Net Getiri (R)':<16} │ {'Kâr Faktörü'}")
    print(mid)
    for r in symbol_reports:
        win_loss_str = f"+{r.avg_win_r:.1f}R / -{r.avg_loss_r:.1f}R"
        print(f"{r.name:<9} │ {r.total_trades:>3} işlem    │ %{r.win_rate_pct:>4.1f}    │ {win_loss_str:<14} │ {r.payoff_ratio:>4.1f}x  │ {r.total_r:>+7.2f} R          │ {r.profit_factor:>4.2f}")
    print(sep)

    # 2. Portföy Geneli Büyük Özet
    print("\n" + sep)
    print("  🏆 TÜM PORTFÖY GENELİ BÜYÜK SONUÇ (16 ENSTRÜMAN BİR ARADA) 🏆  ")
    print(sep)
    print(f"{'Toplam İşlem Sayısı (N)':<38} : {portfolio_report.total_trades} İşlem (5 yılda varlık başı ~15-20 işlem)")
    print(f"{'Kazanan / Kaybeden':<38} : {portfolio_report.wins} Win / {portfolio_report.losses} Loss")
    print(f"{'Kazanma Oranı (Win Rate)':<38} : %{portfolio_report.win_rate_pct:.1f} (Beklenen: %35 - %40)")
    print(f"{'Ortalama Kazanan İşlem (Avg Win)':<38} : +{portfolio_report.avg_win_r:.2f} R")
    print(f"{'Ortalama Kaybeden İşlem (Avg Loss)':<38} : -{portfolio_report.avg_loss_r:.2f} R")
    print(f"{'Asimetrik Oran (Payoff Ratio)':<38} : {portfolio_report.payoff_ratio:.2f}x (Ort. Kâr / Ort. Zarar)")
    print(f"{'Matematiksel Beklenti (Expectancy)':<38} : {portfolio_report.expectancy_r:>+5.2f} R / işlem")
    print(f"{'Kâr Faktörü (Profit Factor)':<38} : {portfolio_report.profit_factor:.2f}")
    print(mid)
    print(f"{'Toplam Net Getiri (Net R)':<38} : {portfolio_report.total_r:>+6.2f} R")
    print(f"{'Hesap Büyümesi (%0.5 Risk ile)':<38} : %{portfolio_report.return_on_capital_pct:>+6.2f} (${portfolio_report.total_pnl_dollars:>+8.2f} Net Kâr)")
    print(f"{'Maksimum Sermaye Çekilmesi (Max DD)':<38} : -{portfolio_report.max_drawdown_r:.1f} R (%{portfolio_report.max_drawdown_pct:.1f})")
    print(f"{'En Uzun Art Arda Kayıp Serisi':<38} : {portfolio_report.max_consecutive_losses} İşlem")
    print(f"{'Ortalama Pozisyon Taşıma Süresi':<38} : {portfolio_report.avg_holding_days:.1f} Gün")

    # Komisyon Hesabı
    comm_per_trade = 0.05 # D1 swing işleminde komisyon/spread yaklaşık 0.05R
    total_comm = round(portfolio_report.total_trades * comm_per_trade, 2)
    net_after_comm = round(portfolio_report.total_r - total_comm, 2)
    print(f"{'Tahmini Komisyon & Spread Maliyeti':<38} : -{total_comm:.2f} R (İhmal edilebilir kurumsal sürtünme)")
    print(f"{'Komisyon Sonrası Gerçek Net R':<38} : {net_after_comm:>+6.2f} R")
    print(sep)

    # 3. Kümülatif Getiri Eğrisi
    print("\n📈 5 YILLIK BİLEŞİK GETİRİ EĞRİSİ (EQUITY CURVE):")
    print(PerformanceReporter.render_ascii_curve(all_portfolio_trades))

    # 4. En Büyük Koşan Kâr Örnekleri (Convexity Runners)
    runners = sorted([t for t in all_portfolio_trades if t.pnl_r >= 3.0], key=lambda x: x.pnl_r, reverse=True)
    print("\n" + "─" * 106)
    print(" 🚀 KÂRI KOŞTURAN EN BÜYÜK ASİMETRİK İŞLEMLER (RUNNERS >= 3.0R):")
    print(f"{'Giriş Tarihi':<12} {'Çıkış Tarihi':<12} {'Varlık':<9} {'Yön':<6} {'Giriş':<10} {'Çıkış':<10} {'Süre (Gün)':<11} {'Zirve R':<9} {'Net P&L (R)'}")
    print("─" * 106)
    for r in runners[:12]:
        print(f"{r.entry_date:<12} {r.exit_date:<12} {r.symbol:<9} {r.direction:<6} {r.entry_price:<10.2f} {r.exit_price:<10.2f} {r.holding_days:<11} {r.peak_r:>+6.1f}R  {r.pnl_r:>+6.2f} R")
    print("─" * 106 + "\n")

    # 5. Benchmark JSON Kaydı
    collector = BenchmarkCollector()
    saved_path = collector.log_backtest_benchmark(
        portfolio_report=portfolio_report,
        config=config,
        symbol_reports=symbol_reports,
        notes="Otomatik 5 Yıllık D1 Çoklu Varlık Backtesti"
    )
    print(f"📁 Benchmark JSON kaydı başarıyla oluşturuldu/güncellendi: {saved_path}\n")

if __name__ == "__main__":
    run_convexity_backtest()
