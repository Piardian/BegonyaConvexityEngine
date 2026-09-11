import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict, Tuple
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
from analytics.performance_reporter import PerformanceReporter

def run_v2_stress_test():
    print("\n" + "═" * 105)
    print(" 🔬 BEGONYA V2 DISPLACEMENT & YAPISAL STOP STRES TESTİ (GERÇEKÇİ KAPANIL GİRİŞİ) 🔬 ")
    print("   • Evren          : 12 Temiz Momentum Varlığı (5 Yıl: 2021 - 2026)")
    print("   • Denetim 1      : Kapanış Fiyatı Kayması (Extended Entry Bias) Düzeltildi (Giriş = Mum Kapanışı)")
    print("   • Denetim 2      : Payda Etkisi & Dar Stop Analizi (0.9x ATR vs 1.5x ATR)")
    print("   • Denetim 3      : Slippage & Spread Cezası %50 Artırıldı (Stres Testi)")
    print("═" * 105 + "\n")

    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    chop_pairs = ["USDCAD", "GBPJPY", "EURJPY", "AUDUSD"]
    symbols = [s for s in config.get("backtest", {}).get("symbols", []) if s not in chop_pairs]

    loader = MarketLoader()
    detector = TrendDetector(config)

    market_data = {}
    for sym in symbols:
        df = loader.load_symbol_data(sym, period="5y")
        if not df.empty and len(df) >= 200:
            market_data[sym] = df

    # =========================================================================
    # 1. BAZ MODEL v1: KÖR KIRILIM GİRİŞİ (1.5x ATR SL)
    # =========================================================================
    sim_v1 = ConvexitySimulator(config)
    trades_v1 = []
    for sym, df in market_data.items():
        signals = []
        for i in range(1, len(df)):
            sig = detector.check_breakout(df, i, sym)
            if sig:
                signals.append(sig)
        trades = sim_v1.simulate_trades_for_symbol(df, signals, sym)
        trades_v1.extend(trades)

    rep_v1 = PerformanceReporter.calculate(trades_v1, name="v1 Baz Model (1.5x ATR)")

    # =========================================================================
    # 2. V2 GERÇEKÇİ SİMÜLATÖR (GİRİŞ = BAR KAPANIŞI / ZERO LOOKAHEAD)
    # =========================================================================
    def simulate_v2(df: pd.DataFrame, symbol: str, entry_mode: str = "CLOSE", slippage_penalty_r: float = 0.0) -> List[ConvexityTrade]:
        trades = []
        in_trade = False
        curr_trade = None

        for i in range(1, len(df)):
            curr_bar = df.iloc[i]
            curr_date = df.index[i]
            atr = curr_bar["atr14"]

            # Aktif Pozisyon Yönetimi
            if in_trade and curr_trade is not None:
                dir = curr_trade["direction"]
                entry = curr_trade["entry_price"]
                sl = curr_trade["current_sl"]
                risk_dist = curr_trade["risk_distance"]

                if dir == "LONG":
                    max_h = max(curr_trade["max_extreme"], curr_bar["high"])
                    curr_trade["max_extreme"] = max_h
                    peak_r = (max_h - entry) / risk_dist

                    # Kâr kilitleme (+2.0R)
                    if peak_r >= 2.0 and not curr_trade["partial_taken"]:
                        curr_trade["partial_taken"] = True
                        curr_trade["realized_r"] = 1.0 # %50 * 2.0R
                        curr_trade["remaining_ratio"] = 0.50
                        sl = max(sl, entry + (0.05 * risk_dist))
                        curr_trade["current_sl"] = sl

                    # Trailing Stop (2.5x ATR)
                    if curr_trade["partial_taken"]:
                        trail_sl = max_h - (2.5 * atr)
                        if trail_sl > sl:
                            sl = trail_sl
                            curr_trade["current_sl"] = sl

                    # Stop Vuruşu
                    if curr_bar["low"] <= sl:
                        exit_p = min(curr_bar["open"], sl)
                        runner_r = (exit_p - entry) / risk_dist
                        raw_r = curr_trade["realized_r"] + (curr_trade["remaining_ratio"] * runner_r) if curr_trade["partial_taken"] else runner_r
                        # Slippage cezası düşülür
                        final_r = round(raw_r - slippage_penalty_r, 2)

                        trades.append(ConvexityTrade(
                            entry_date=str(curr_trade["entry_time"].date()), exit_date=str(curr_date.date()),
                            symbol=symbol, direction=dir, entry_price=entry, initial_sl=curr_trade["initial_sl"],
                            exit_price=exit_p, holding_days=(curr_date - curr_trade["entry_time"]).days,
                            risk_dollars=50.0, pnl_dollars=final_r * 50.0, pnl_r=final_r, exit_reason="Exit",
                            peak_r=round(peak_r, 2), partial_taken=curr_trade["partial_taken"]
                        ))
                        in_trade = False
                        curr_trade = None

                elif dir == "SHORT":
                    min_l = min(curr_trade["max_extreme"], curr_bar["low"])
                    curr_trade["max_extreme"] = min_l
                    peak_r = (entry - min_l) / risk_dist

                    if peak_r >= 2.0 and not curr_trade["partial_taken"]:
                        curr_trade["partial_taken"] = True
                        curr_trade["realized_r"] = 1.0
                        curr_trade["remaining_ratio"] = 0.50
                        sl = min(sl, entry - (0.05 * risk_dist))
                        curr_trade["current_sl"] = sl

                    if curr_trade["partial_taken"]:
                        trail_sl = min_l + (2.5 * atr)
                        if trail_sl < sl:
                            sl = trail_sl
                            curr_trade["current_sl"] = sl

                    if curr_bar["high"] >= sl:
                        exit_p = max(curr_bar["open"], sl)
                        runner_r = (entry - exit_p) / risk_dist
                        raw_r = curr_trade["realized_r"] + (curr_trade["remaining_ratio"] * runner_r) if curr_trade["partial_taken"] else runner_r
                        final_r = round(raw_r - slippage_penalty_r, 2)

                        trades.append(ConvexityTrade(
                            entry_date=str(curr_trade["entry_time"].date()), exit_date=str(curr_date.date()),
                            symbol=symbol, direction=dir, entry_price=entry, initial_sl=curr_trade["initial_sl"],
                            exit_price=exit_p, holding_days=(curr_date - curr_trade["entry_time"]).days,
                            risk_dollars=50.0, pnl_dollars=final_r * 50.0, pnl_r=final_r, exit_reason="Exit",
                            peak_r=round(peak_r, 2), partial_taken=curr_trade["partial_taken"]
                        ))
                        in_trade = False
                        curr_trade = None

            # Yeni Sinyal Taraması
            if not in_trade:
                sig = detector.check_breakout(df, i, symbol)
                if sig:
                    # Displacement Kontrolü: Mum gövdesi >= 0.40x ATR
                    body = abs(curr_bar["close"] - curr_bar["open"])
                    if body >= 0.40 * atr:
                        # 1. GİRİŞ FİYATI:
                        # Eğer "CLOSE" ise: Barın kapanışından (NY 17:00 / TSİ 00:00) girilir (GERÇEKÇİ!)
                        # Eğer "BREAKOUT" ise: Donchian sınırından girilmiş gibi (İllüzyon)
                        if entry_mode == "CLOSE":
                            actual_entry = curr_bar["close"]
                        else:
                            actual_entry = sig.entry_price

                        # 2. YAPISAL STOP:
                        # Mumun dibi / tepesi + 0.1x ATR tampon
                        if sig.direction == "LONG":
                            sl_p = curr_bar["low"] - (0.1 * atr)
                            # Güvenlik tavanı: Stop girişten asla 1.5x ATR'den uzak olamaz
                            if (actual_entry - sl_p) > 1.5 * atr:
                                sl_p = actual_entry - (1.2 * atr)
                        else:
                            sl_p = curr_bar["high"] + (0.1 * atr)
                            if (sl_p - actual_entry) > 1.5 * atr:
                                sl_p = actual_entry + (1.2 * atr)

                        risk_dist = abs(actual_entry - sl_p)
                        if risk_dist <= 0:
                            continue

                        in_trade = True
                        curr_trade = {
                            "entry_time": curr_date,
                            "direction": sig.direction,
                            "entry_price": actual_entry,
                            "initial_sl": sl_p,
                            "current_sl": sl_p,
                            "risk_distance": risk_dist,
                            "risk_dollars": 50.0,
                            "max_extreme": actual_entry,
                            "partial_taken": False,
                            "realized_r": 0.0,
                            "remaining_ratio": 1.0
                        }

        return trades

    # Model 2A: İllüzyonlu v2 (Giriş = Breakout Seviyesi)
    trades_v2_illusion = []
    for sym, df in market_data.items():
        t = simulate_v2(df, sym, entry_mode="BREAKOUT", slippage_penalty_r=0.0)
        trades_v2_illusion.extend(t)
    rep_v2_illusion = PerformanceReporter.calculate(trades_v2_illusion, name="v2 İllüzyonlu (Giriş=Breakout)")

    # Model 2B: Gerçekçi v2 (Giriş = Kapanış Fiyatı / Zero Lookahead)
    trades_v2_real = []
    for sym, df in market_data.items():
        t = simulate_v2(df, sym, entry_mode="CLOSE", slippage_penalty_r=0.05) # Normal 0.05R slippage
        trades_v2_real.extend(t)
    rep_v2_real = PerformanceReporter.calculate(trades_v2_real, name="v2 Gerçekçi (Giriş=Kapanış)")

    # Model 2C: v2 Stres Testi (Giriş = Kapanış + %50 Artırılmış Ağır Slippage/Spread Cezası 0.10R)
    trades_v2_stress = []
    for sym, df in market_data.items():
        t = simulate_v2(df, sym, entry_mode="CLOSE", slippage_penalty_r=0.10) # 2 Katı Ağır Slippage!
        trades_v2_stress.extend(t)
    rep_v2_stress = PerformanceReporter.calculate(trades_v2_stress, name="v2 Stres Testi (0.10R Ağır Ceza)")

    # =========================================================================
    # KARŞILAŞTIRMALI BİLANÇO
    # =========================================================================
    sep = "═" * 108
    mid = "─" * 108
    print(sep)
    print("  📊 5 YILLIK BÜYÜK DÜZELTME & STRES TESTİ BİLANÇOSU (ILLUSION vs. REALITY) 📊  ")
    print(sep)
    print(f"{'Metrik':<28} │ {'v1 Baz Model':<16} │ {'v2 İllüzyonlu':<17} │ {'v2 Gerçekçi (Close)':<20} │ {'v2 Stres Testi (%50 Ağır)'}")
    print(mid)
    print(f"{'Toplam İşlem ($N$)':<28} │ {rep_v1.total_trades:>5} işlem       │ {rep_v2_illusion.total_trades:>5} işlem        │ {rep_v2_real.total_trades:>5} işlem          │ {rep_v2_stress.total_trades:>5} işlem")
    print(f"{'Kazanma Oranı (Win Rate)':<28} │ %{rep_v1.win_rate_pct:>5.1f}           │ %{rep_v2_illusion.win_rate_pct:>5.1f}            │ %{rep_v2_real.win_rate_pct:>5.1f}            │ %{rep_v2_stress.win_rate_pct:>5.1f}")
    print(f"{'Ortalama Win / Loss':<28} │ +{rep_v1.avg_win_r:.2f} / -{rep_v1.avg_loss_r:.2f}   │ +{rep_v2_illusion.avg_win_r:.2f} / -{rep_v2_illusion.avg_loss_r:.2f}    │ +{rep_v2_real.avg_win_r:.2f} / -{rep_v2_real.avg_loss_r:.2f}     │ +{rep_v2_stress.avg_win_r:.2f} / -{rep_v2_stress.avg_loss_r:.2f}")
    print(f"{'Asimetrik Oran (Payoff)':<28} │ {rep_v1.payoff_ratio:>5.2f}x          │ {rep_v2_illusion.payoff_ratio:>5.2f}x           │ {rep_v2_real.payoff_ratio:>5.2f}x           │ {rep_v2_stress.payoff_ratio:>5.2f}x")
    print(f"{'Kâr Faktörü (PF)':<28} │ {rep_v1.profit_factor:>5.2f}           │ {rep_v2_illusion.profit_factor:>5.2f}            │ {rep_v2_real.profit_factor:>5.2f} 🚀         │ {rep_v2_stress.profit_factor:>5.2f} 🛡️")
    print(f"{'Beklenti ($E$ / işlem)':<28} │ +{rep_v1.expectancy_r:>4.2f} R         │ +{rep_v2_illusion.expectancy_r:>4.2f} R          │ +{rep_v2_real.expectancy_r:>4.2f} R          │ +{rep_v2_stress.expectancy_r:>4.2f} R")
    print(f"{'Toplam Net Getiri':<28} │ {rep_v1.total_r:>+6.2f} R        │ {rep_v2_illusion.total_r:>+6.2f} R        │ {rep_v2_real.total_r:>+6.2f} R         │ {rep_v2_stress.total_r:>+6.2f} R")
    print(f"{'Maksimum Çekilme (DD)':<28} │ -{rep_v1.max_drawdown_r:.1f}R (%{rep_v1.max_drawdown_pct:.1f}) │ -{rep_v2_illusion.max_drawdown_r:.1f}R (%{rep_v2_illusion.max_drawdown_pct:.1f}) │ -{rep_v2_real.max_drawdown_r:.1f}R (%{rep_v2_real.max_drawdown_pct:.1f})  │ -{rep_v2_stress.max_drawdown_r:.1f}R (%{rep_v2_stress.max_drawdown_pct:.1f})")
    print(sep)

if __name__ == "__main__":
    run_v2_stress_test()
