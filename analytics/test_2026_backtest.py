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

def run_2026_backtest():
    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    symbols = config.get("backtest", {}).get("symbols", [])
    initial_capital = config.get("backtest", {}).get("initial_capital", 10000.0)
    risk_pct = config.get("backtest", {}).get("risk_per_trade_pct", 0.5)

    loader = MarketLoader()
    detector = TrendDetector(config)
    simulator = ConvexitySimulator(config)

    all_2026_trades: List[ConvexityTrade] = []
    symbol_reports: List[ConvexityReport] = []
    symbol_trades_map: Dict[str, List[ConvexityTrade]] = {}

    for symbol in symbols:
        df = loader.load_symbol_data(symbol, period="5y")
        if df.empty or len(df) < 200:
            continue

        # Tüm tarihçede sinyalleri tespit et (EMA200 ve Donchian tam otursun)
        signals = []
        for i in range(1, len(df)):
            sig = detector.check_breakout(df, i, symbol)
            if sig:
                signals.append(sig)

        # Simülasyonu koştur
        full_trades = simulator.simulate_trades_for_symbol(df, signals, symbol)

        # SADECE 2026 yılında açılan işlemleri filtrele
        trades_2026 = [t for t in full_trades if t.entry_date >= "2026-01-01"]
        symbol_trades_map[symbol] = trades_2026
        all_2026_trades.extend(trades_2026)

        rep = PerformanceReporter.calculate(trades_2026, name=symbol, initial_capital=initial_capital)
        symbol_reports.append(rep)

    if not all_2026_trades:
        print("❌ 2026 yılında hiçbir sembolde işlem oluşmadı.")
        return

    # Tarihe göre sırala
    all_2026_trades.sort(key=lambda t: t.entry_date)
    portfolio_report = PerformanceReporter.calculate(
        all_2026_trades,
        name="2026 YTD Unified Portfolio",
        initial_capital=initial_capital
    )

    sep = "═" * 108
    mid = "─" * 108

    # =========================================================================
    # 1. BAŞLIK
    # =========================================================================
    print("\n" + sep)
    print(" 🌺 BEGONYA CONVEXITY ENGINE │ 2026 YILI DETAYLI PERFORMANS VE DENETİM KARNESİ 🌺 ")
    print(f"   • Test Dönemi        : 01 Ocak 2026 - 10 Eylül 2026 (~8.5 Ay / YTD)")
    print(f"   • Portföy Evreni     : 16 Küresel Enstrüman (FX, Değerli Metaller, Emtia, Endeksler, Kripto)")
    print(f"   • Risk Yönetimi      : Sabit %0.50 Risk / İşlem ($10,000 Hesapta $50.00 Risk)")
    print(f"   • Asimetri Kuralı    : +2.0R'da %50 Kâr Kilitleme | Kalan %50 2.5x ATR Trailing Stop")
    print(sep + "\n")

    # =========================================================================
    # 2. PORTFÖY GENELİ BÜYÜK SONUÇ
    # =========================================================================
    total_trades = portfolio_report.total_trades
    wins = portfolio_report.wins
    losses = portfolio_report.losses
    win_rate = portfolio_report.win_rate_pct
    net_r = portfolio_report.total_r
    net_usd = portfolio_report.total_pnl_dollars
    ret_pct = portfolio_report.return_on_capital_pct
    max_dd_r = portfolio_report.max_drawdown_r
    max_dd_pct = portfolio_report.max_drawdown_pct
    profit_factor = portfolio_report.profit_factor
    payoff = portfolio_report.payoff_ratio
    expectancy = portfolio_report.expectancy_r
    comm_est_r = round(total_trades * 0.05, 2)
    net_after_comm_r = round(net_r - comm_est_r, 2)
    net_after_comm_usd = round(net_after_comm_r * (initial_capital * 0.005), 2)

    print("📊 [BÖLÜM 1] 2026 PORTFÖY GENELİ KÜMÜLATİF METRİKLER")
    print(mid)
    col1_w = 38
    print(f"{'Toplam Açılan İşlem Sayısı (N)':<{col1_w}} : {total_trades} İşlem")
    print(f"{'Kazanan / Kaybeden Dağılımı':<{col1_w}} : {wins} Kazanç (Win) │ {losses} Kayıp (Loss)")
    print(f"{'Kazanma Oranı (Win Rate)':<{col1_w}} : %{win_rate:.1f}")
    print(f"{'Ortalama Kazanan İşlem (Avg Win)':<{col1_w}} : +{portfolio_report.avg_win_r:.2f} R (+${portfolio_report.avg_win_r * 50:.2f})")
    print(f"{'Ortalama Kaybeden İşlem (Avg Loss)':<{col1_w}} : -{portfolio_report.avg_loss_r:.2f} R (-${portfolio_report.avg_loss_r * 50:.2f})")
    print(f"{'Asimetrik Oran (Payoff Ratio)':<{col1_w}} : {payoff:.2f}x (Ort. Kâr / Ort. Zarar)")
    print(f"{'İşlem Başı Matematiksel Beklenti':<{col1_w}} : {expectancy:>+5.2f} R / işlem (+${expectancy * 50:.2f})")
    print(f"{'Kâr Faktörü (Profit Factor)':<{col1_w}} : {profit_factor:.2f}")
    print(mid)
    print(f"{'Brüt Toplam Getiri (Net R)':<{col1_w}} : {net_r:>+6.2f} R")
    print(f"{'Tahmini Komisyon & Slippage Maliyeti':<{col1_w}} : -{comm_est_r:.2f} R")
    print(f"{'Net Gerçekleşen Getiri (Komisyon Sonrası)':<{col1_w}} : {net_after_comm_r:>+6.2f} R ({net_after_comm_usd:>+8.2f} USD)")
    print(f"{'Sermaye Büyümesi (%0.5 Risk ile)':<{col1_w}} : %{ret_pct:>+6.2f} (${net_usd:>+8.2f} Net Kâr)")
    print(f"{'Maksimum Sermaye Çekilmesi (Max DD)':<{col1_w}} : -{max_dd_r:.2f} R (%{max_dd_pct:.2f})")
    print(f"{'En Uzun Art Arda Kayıp Serisi':<{col1_w}} : {portfolio_report.max_consecutive_losses} İşlem")
    print(f"{'Ortalama Pozisyon Taşıma Süresi':<{col1_w}} : {portfolio_report.avg_holding_days:.1f} Gün")
    print(sep + "\n")

    # =========================================================================
    # 3. 16 ENSTRÜMAN BAZINDA TEK TEK 2026 KARNESİ
    # =========================================================================
    print("📋 [BÖLÜM 2] 16 ENSTRÜMAN BAZINDA TEK TEK 2026 KARNESİ")
    print(sep)
    print(f"{'Varlık':<9} │ {'İşlem':<6} │ {'Win %':<7} │ {'Ort.Kâr/Zarar':<15} │ {'Payoff':<7} │ {'Net Getiri (R)':<16} │ {'Net PnL ($)':<12} │ {'Kâr Fakt':<9} │ {'Max DD (R)'}")
    print(mid)

    symbol_reports.sort(key=lambda r: r.total_r, reverse=True)
    for r in symbol_reports:
        win_loss_str = f"+{r.avg_win_r:.1f}R / -{r.avg_loss_r:.1f}R"
        pnl_usd_str = f"${r.total_pnl_dollars:>+8.2f}"
        print(f"{r.name:<9} │ {r.total_trades:>4}   │ %{r.win_rate_pct:>4.1f} │ {win_loss_str:<15} │ {r.payoff_ratio:>4.1f}x │ {r.total_r:>+7.2f} R          │ {pnl_usd_str:<12} │ {r.profit_factor:>6.2f}   │ -{r.max_drawdown_r:.1f} R")
    print(sep + "\n")

    # =========================================================================
    # 4. VARLIK SINIFLARI KIRILIMI (ASSET CLASS BREAKDOWN)
    # =========================================================================
    asset_groups = {
        "Forex Majörleri (EUR, GBP, AUD, CAD, CHF)": ["EURUSD", "GBPUSD", "AUDUSD", "USDCAD", "USDCHF"],
        "JPY Çaprazları (USDJPY, EURJPY, GBPJPY)": ["USDJPY", "EURJPY", "GBPJPY"],
        "Değerli Metaller (Altın & Gümüş)": ["XAUUSD", "SILVER"],
        "Emtia (Ham Petrol)": ["CL_OIL"],
        "Küresel Hisse Endeksleri (Nasdaq & S&P)": ["NAS100", "SP500"],
        "Kripto Varlıklar (BTC, ETH, SOL)": ["BTCUSD", "ETHUSD", "SOLUSD"]
    }

    print("🏛️ [BÖLÜM 3] 2026 VARLIK GRUPLARI (SEKTÖR) PERFORMANS DAĞILIMI")
    print(mid)
    print(f"{'Varlık Grubu':<45} │ {'İşlem':<7} │ {'Win %':<8} │ {'Toplam R':<12} │ {'Dolar PnL':<12} │ {'Kâr Faktörü'}")
    print(mid)

    for group_name, group_syms in asset_groups.items():
        g_trades = [t for t in all_2026_trades if t.symbol in group_syms]
        if g_trades:
            g_rep = PerformanceReporter.calculate(g_trades, name=group_name, initial_capital=initial_capital)
            print(f"{group_name:<45} │ {g_rep.total_trades:>5}   │ %{g_rep.win_rate_pct:>5.1f}  │ {g_rep.total_r:>+7.2f} R   │ ${g_rep.total_pnl_dollars:>+8.2f}   │ {g_rep.profit_factor:>6.2f}")
    print(sep + "\n")

    # =========================================================================
    # 5. AYLIK GETİRİ ANALİZİ (MONTHLY BREAKDOWN)
    # =========================================================================
    print("📅 [BÖLÜM 4] 2026 AYLIK GETİRİ VE PERFORMANS DAĞILIMI")
    print(mid)
    print(f"{'Ay':<12} │ {'Kapanan İşlem':<15} │ {'Win %':<10} │ {'Net Getiri (R)':<16} │ {'Net Getiri ($)':<16} │ {'Kâr Faktörü'}")
    print(mid)

    month_names = {
        "2026-01": "Ocak 2026", "2026-02": "Şubat 2026", "2026-03": "Mart 2026",
        "2026-04": "Nisan 2026", "2026-05": "Mayıs 2026", "2026-06": "Haziran 2026",
        "2026-07": "Temmuz 2026", "2026-08": "Ağustos 2026", "2026-09": "Eylül 2026 (YTD)"
    }

    for m_key, m_label in month_names.items():
        m_trades = [t for t in all_2026_trades if t.exit_date.startswith(m_key)]
        if m_trades:
            m_rep = PerformanceReporter.calculate(m_trades, name=m_label, initial_capital=initial_capital)
            print(f"{m_label:<12} │ {m_rep.total_trades:>8}        │ %{m_rep.win_rate_pct:>5.1f}    │ {m_rep.total_r:>+7.2f} R          │ ${m_rep.total_pnl_dollars:>+9.2f}        │ {m_rep.profit_factor:>6.2f}")
        else:
            print(f"{m_label:<12} │ {'0':>8}        │ %  0.0    │   +0.00 R          │ $    +0.00        │   0.00")
    print(sep + "\n")

    # =========================================================================
    # 6. EN BÜYÜK KOŞAN KÂRLAR VE EN BÜYÜK ZARARLAR
    # =========================================================================
    print("🚀 [BÖLÜM 5] 2026'NIN EN BÜYÜK ASİMETRİK KOŞUCULARI (TOP RUNNERS)")
    print(mid)
    print(f"{'Giriş':<11} {'Çıkış':<11} {'Varlık':<8} {'Yön':<6} {'Giriş Fiyatı':<14} {'Çıkış Fiyatı':<14} {'Süre(G)':<8} {'Zirve R':<8} {'Net R':<9} {'Net PnL ($)'}")
    print(mid)
    top_winners = sorted(all_2026_trades, key=lambda t: t.pnl_r, reverse=True)[:6]
    for w in top_winners:
        print(f"{w.entry_date:<11} {w.exit_date:<11} {w.symbol:<8} {w.direction:<6} {w.entry_price:<14.2f} {w.exit_price:<14.2f} {w.holding_days:<8} {w.peak_r:>+5.1f}R  {w.pnl_r:>+6.2f}R  +${w.pnl_dollars:.2f}")
    print(mid + "\n")

    print("🛑 [BÖLÜM 6] 2026'NIN EN BÜYÜK ZARARLARI (STOP LOSS DETAYLARI)")
    print(mid)
    print(f"{'Giriş':<11} {'Çıkış':<11} {'Varlık':<8} {'Yön':<6} {'Giriş Fiyatı':<14} {'Çıkış Fiyatı':<14} {'Süre(G)':<8} {'Net R':<9} {'Net PnL ($)':<12} {'Çıkış Nedeni'}")
    print(mid)
    top_losers = sorted(all_2026_trades, key=lambda t: t.pnl_r)[:6]
    for l in top_losers:
        print(f"{l.entry_date:<11} {l.exit_date:<11} {l.symbol:<8} {l.direction:<6} {l.entry_price:<14.2f} {l.exit_price:<14.2f} {l.holding_days:<8} {l.pnl_r:>+6.2f}R  -${abs(l.pnl_dollars):.2f}       {l.exit_reason}")
    print(sep + "\n")

    # =========================================================================
    # 7. 2026 GETİRİ EĞRİSİ (EQUITY CURVE)
    # =========================================================================
    print("📈 [BÖLÜM 7] 2026 YILI KÜMÜLATİF GETİRİ EĞRİSİ (EQUITY CURVE)")
    print(PerformanceReporter.render_ascii_curve(all_2026_trades, height=10, width=60))
    print(sep + "\n")

    # =========================================================================
    # 8. BENCHMARK KAYDI
    # =========================================================================
    collector = BenchmarkCollector()
    saved_path = collector.log_backtest_benchmark(
        portfolio_report=portfolio_report,
        config=config,
        symbol_reports=symbol_reports,
        notes="2026 YTD Özel Yıl Denetim Testi (01 Ocak 2026 - 10 Eylül 2026)"
    )
    print(f"📁 2026 Benchmark kaydı 'analytics/benchmarks/backtest_history.json' kütüğüne kaydedildi: {saved_path}\n")

if __name__ == "__main__":
    run_2026_backtest()
