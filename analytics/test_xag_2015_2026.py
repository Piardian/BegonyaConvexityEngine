import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict
import pandas as pd
import numpy as np
import yfinance as yf

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

from engine.trend_detector import TrendDetector
from engine.convexity_simulator import ConvexitySimulator, ConvexityTrade
from analytics.performance_reporter import PerformanceReporter, ConvexityReport
from analytics.benchmark_collector import BenchmarkCollector

logging.basicConfig(level=logging.WARNING)

def load_silver_full_data(cache_dir: Path) -> pd.DataFrame:
    """2014-01-01'den günümüze Gümüş (SI=F) verisini çeker ve göstergeleri hesaplar."""
    cache_file = cache_dir / "SILVER_2014_2026.csv"
    df = pd.DataFrame()

    if cache_file.exists():
        try:
            df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
        except Exception:
            df = pd.DataFrame()

    if df.empty or len(df) < 1000:
        df = yf.download("SI=F", start="2014-01-01", end="2026-09-11", interval="1d", progress=False)
        if df.empty:
            return pd.DataFrame()

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() for c in df.columns]

        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        df.sort_index(inplace=True)
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)

        df.to_csv(cache_file)

    # Göstergeleri hesapla
    high = df["high"]
    low = df["low"]
    close_prev = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low - close_prev).abs()
    ], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(window=14).mean()

    # Donchian 20 (Lookahead-free: shift(1))
    df["donchian_high_20"] = df["high"].rolling(window=20).max().shift(1)
    df["donchian_low_20"] = df["low"].rolling(window=20).min().shift(1)

    # EMA 20 & 200
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    df.dropna(subset=["atr14", "donchian_high_20", "donchian_low_20", "ema200"], inplace=True)
    return df

def run_xag_multiyear_backtest():
    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    initial_capital = config.get("backtest", {}).get("initial_capital", 10000.0)
    cache_dir = PROJECT_DIR / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    detector = TrendDetector(config)
    simulator = ConvexitySimulator(config)

    df = load_silver_full_data(cache_dir)
    if df.empty or len(df) < 500:
        print("❌ Gümüş (XAG) verisi yüklenemedi.")
        return

    # Sinyalleri tespit et
    signals = []
    for i in range(1, len(df)):
        sig = detector.check_breakout(df, i, "SILVER")
        if sig:
            signals.append(sig)

    # Simülasyonu çalıştır
    all_trades = simulator.simulate_trades_for_symbol(df, signals, "SILVER")

    # 2015 ve sonrası işlemleri filtrele
    trades_from_2015 = [t for t in all_trades if t.entry_date >= "2015-01-01"]
    trades_from_2015.sort(key=lambda t: t.entry_date)

    sep = "═" * 106
    mid = "─" * 106

    print("\n" + sep)
    print(" 🥈 BEGONYA CONVEXITY │ GÜMÜŞ (XAG / SILVER) 12 YILLIK (2015 - 2026) TARİHSEL BACKTESTİ 🥈 ")
    print("   • Enstrüman          : Gümüş (XAGUSD / SI=F)")
    print("   • Test Dönemi        : 01 Ocak 2015 - 10 Eylül 2026 (Tam 12 Yıl / 2014 Warm-Up)")
    print("   • Risk Yönetimi      : Sabit %0.50 Risk / İşlem ($10,000 Hesapta $50.00)")
    print("   • Strateji Mekaniği  : 20G Donchian + 200 EMA + %50 Kâr Kilit @ 2.0R + 2.5x ATR Trailing")
    print(sep + "\n")

    # 1. Yıl Yıl Karne Raporu
    years = list(range(2015, 2027))
    yearly_reports = []

    print("📋 [BÖLÜM 1] YIL YIL AYRI AYRI XAG PERFORMANS KARNESİ (2015 - 2026)")
    print(sep)
    print(f"{'Yıl':<7} │ {'İşlem':<6} │ {'Win %':<8} │ {'Ort.Kâr/Zarar':<16} │ {'Payoff':<7} │ {'Net Getiri (R)':<16} │ {'Net PnL ($)':<12} │ {'Kâr Fakt':<9} │ {'Max DD'}")
    print(mid)

    for yr in years:
        yr_str = str(yr)
        yr_trades = [t for t in trades_from_2015 if t.entry_date.startswith(yr_str)]
        yr_label = f"{yr} (YTD)" if yr == 2026 else str(yr)

        if yr_trades:
            rep = PerformanceReporter.calculate(yr_trades, name=yr_label, initial_capital=initial_capital)
            yearly_reports.append(rep)
            win_loss_str = f"+{rep.avg_win_r:.1f}R / -{rep.avg_loss_r:.1f}R"
            pnl_usd_str = f"${rep.total_pnl_dollars:>+8.2f}"
            print(f"{yr_label:<7} │ {rep.total_trades:>4}   │ %{rep.win_rate_pct:>5.1f}  │ {win_loss_str:<16} │ {rep.payoff_ratio:>4.1f}x │ {rep.total_r:>+7.2f} R          │ {pnl_usd_str:<12} │ {rep.profit_factor:>6.2f}   │ -{rep.max_drawdown_r:.1f} R")
        else:
            print(f"{yr_label:<7} │    0   │ %  0.0  │   +0.0R / -0.0R    │  0.0x │    +0.00 R          │ $    +0.00   │   0.00   │ -0.0 R")

    print(sep + "\n")

    # 2. 12 Yıllık Portföy Kümülatif Özeti
    portfolio_report = PerformanceReporter.calculate(
        trades_from_2015,
        name="12-Year XAG Portfolio",
        initial_capital=initial_capital
    )

    total_trades = portfolio_report.total_trades
    wins = portfolio_report.wins
    losses = portfolio_report.losses
    win_rate = portfolio_report.win_rate_pct
    net_r = portfolio_report.total_r
    net_usd = portfolio_report.total_pnl_dollars
    ret_pct = portfolio_report.return_on_capital_pct
    max_dd_r = portfolio_report.max_drawdown_r
    max_dd_pct = portfolio_report.max_drawdown_pct
    pf = portfolio_report.profit_factor
    payoff = portfolio_report.payoff_ratio
    expectancy = portfolio_report.expectancy_r
    comm_est_r = round(total_trades * 0.05, 2)
    net_after_comm_r = round(net_r - comm_est_r, 2)
    net_after_comm_usd = round(net_after_comm_r * (initial_capital * 0.005), 2)

    profitable_years = len([r for r in yearly_reports if r.total_r > 0])
    losing_years = len([r for r in yearly_reports if r.total_r < 0])

    print("🏆 [BÖLÜM 2] 12 YILLIK GENEL KÜMÜLATİF SONUÇ (2015 - 2026)")
    print(mid)
    w = 40
    print(f"{'Toplam İşlem Sayısı (N)':<{w}} : {total_trades} İşlem (12 yılda yılda ortalama ~8 işlem)")
    print(f"{'Kazanan / Kaybeden Dağılımı':<{w}} : {wins} Win / {losses} Loss")
    print(f"{'Kazanma Oranı (Win Rate)':<{w}} : %{win_rate:.1f}")
    print(f"{'Ortalama Kazanç (Avg Win)':<{w}} : +{portfolio_report.avg_win_r:.2f} R (+${portfolio_report.avg_win_r * 50:.2f})")
    print(f"{'Ortalama Kayıp (Avg Loss)':<{w}} : -{portfolio_report.avg_loss_r:.2f} R (-${portfolio_report.avg_loss_r * 50:.2f})")
    print(f"{'Asimetrik Oran (Payoff Ratio)':<{w}} : {payoff:.2f}x (Ort. Kâr / Ort. Zarar)")
    print(f"{'İşlem Başı Matematiksel Beklenti':<{w}} : {expectancy:>+5.2f} R / işlem (+${expectancy * 50:.2f})")
    print(f"{'Kâr Faktörü (Profit Factor)':<{w}} : {pf:.2f}")
    print(f"{'Yıllık Başarı Dağılımı':<{w}} : {profitable_years} Kârlı Yıl │ {losing_years} Kayıplı Yıl")
    print(mid)
    print(f"{'Brüt Toplam Net Getiri (Net R)':<{w}} : {net_r:>+6.2f} R")
    print(f"{'Tahmini Komisyon & Spread Maliyeti':<{w}} : -{comm_est_r:.2f} R")
    print(f"{'Komisyon Sonrası Gerçek Net R':<{w}} : {net_after_comm_r:>+6.2f} R ({net_after_comm_usd:>+8.2f} USD)")
    print(f"{'Sermaye Büyümesi (%0.5 Risk ile)':<{w}} : %{ret_pct:>+6.2f} (${net_usd:>+8.2f} Net Kâr)")
    print(f"{'Maksimum Sermaye Çekilmesi (Max DD)':<{w}} : -{max_dd_r:.2f} R (%{max_dd_pct:.2f})")
    print(f"{'En Uzun Art Arda Kayıp Serisi':<{w}} : {portfolio_report.max_consecutive_losses} İşlem")
    print(f"{'Ortalama Pozisyon Taşıma Süresi':<{w}} : {portfolio_report.avg_holding_days:.1f} Gün")
    print(sep + "\n")

    # 3. En Büyük Koşan İşlemler (Top Runners)
    print("🚀 [BÖLÜM 3] GÜMÜŞÜN 12 YILDAKİ EN BÜYÜK KOŞAN İŞLEMLERİ (TOP RUNNERS)")
    print(mid)
    print(f"{'Giriş':<11} {'Çıkış':<11} {'Yön':<6} {'Giriş':<11} {'Çıkış':<11} {'Süre(G)':<8} {'Zirve R':<8} {'Net R':<9} {'Net PnL ($)'}")
    print(mid)
    top_winners = sorted(trades_from_2015, key=lambda t: t.pnl_r, reverse=True)[:10]
    for w in top_winners:
        print(f"{w.entry_date:<11} {w.exit_date:<11} {w.direction:<6} {w.entry_price:<11.2f} {w.exit_price:<11.2f} {w.holding_days:<8} {w.peak_r:>+5.1f}R  {w.pnl_r:>+6.2f}R  +${w.pnl_dollars:.2f}")
    print(sep + "\n")

    # 4. Kümülatif Getiri Eğrisi
    print("📈 [BÖLÜM 4] 12 YILLIK XAG BİLEŞİK GETİRİ EĞRİSİ (2015 - 2026)")
    print(PerformanceReporter.render_ascii_curve(trades_from_2015, height=12, width=65))
    print(sep + "\n")

    # 5. Benchmark Kaydı
    collector = BenchmarkCollector()
    saved_path = collector.log_backtest_benchmark(
        portfolio_report=portfolio_report,
        config={**config, "symbols": ["SILVER"]},
        symbol_reports=yearly_reports,
        notes="XAG Gümüş 12 Yıllık Yıl Bazlı Özel Backtest (2015 - 2026)"
    )
    print(f"📁 Benchmark kaydı arşivlendi: {saved_path}\n")

if __name__ == "__main__":
    run_xag_multiyear_backtest()
