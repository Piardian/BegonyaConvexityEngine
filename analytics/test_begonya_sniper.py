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

def run_sniper_experiment():
    print("\n" + "═" * 105)
    print(" 🎯 BEGONYA SMC SNIPER GİRİŞ SİMÜLASYONU VE KARŞILAŞTIRMALI KARNE 🎯 ")
    print("   • Evren      : 12 Temiz Momentum Varlığı (Altın, Petrol, Gümüş, Kripto, Endeksler, FX)")
    print("   • Dönem      : 2021 - 2026 (5 Yıl)")
    print("   • Deney A (Mevcut Model) : Kör D1 Market Girişi (1.5x ATR SL, 2.0R TP, 2.5x ATR Trailing)")
    print("   • Deney B (Begonya Sniper Retest) : Kırılan seviyeye (SMC FVG/Retest) Limit Emir + Dar Stop (0.8x ATR SL)")
    print("   • Deney C (Displacement & Dar SL) : Güçlü Mum Gövdesi Filtresi + Kırılım Mumu Dibi SL (Dar Stop)")
    print("═" * 105 + "\n")

    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    chop_pairs = ["USDCAD", "GBPJPY", "EURJPY", "AUDUSD"]
    symbols = [s for s in config.get("backtest", {}).get("symbols", []) if s not in chop_pairs]

    loader = MarketLoader()
    detector = TrendDetector(config)

    # Verileri önbelleğe al
    market_data = {}
    for sym in symbols:
        df = loader.load_symbol_data(sym, period="5y")
        if not df.empty and len(df) >= 200:
            market_data[sym] = df

    # =========================================================================
    # MODEL A: MEVCUT BAZ MODEL (Market Kırılım Girişi)
    # =========================================================================
    sim_baseline = ConvexitySimulator(config)
    trades_baseline = []
    for sym, df in market_data.items():
        signals = []
        for i in range(1, len(df)):
            sig = detector.check_breakout(df, i, sym)
            if sig:
                signals.append(sig)
        trades = sim_baseline.simulate_trades_for_symbol(df, signals, sym)
        trades_baseline.extend(trades)

    rep_baseline = PerformanceReporter.calculate(trades_baseline, name="Baz Model (Kör Market Girişi)")

    # =========================================================================
    # MODEL B: BEGONYA SMC SNIPER RETEST (Limit Emirle Geri Çekilmede Dar Stop)
    # Mantık: Kırılım olduktan sonra kör market girme; kırılan Donchian seviyesine
    # (veya 0.3x ATR FVG bölgesine) limit alış emri koy.
    # Stopu 1.5x ATR yerine sadece 0.8x ATR dar tut. (Eğer 3 gün içinde gelmezse iptal).
    # =========================================================================
    def simulate_sniper_retest(df: pd.DataFrame, symbol: str) -> List[ConvexityTrade]:
        trades = []
        in_trade = False
        curr_trade = None
        pending_order = None # {"direction", "limit_price", "sl_price", "expire_idx", "risk_dist"}

        for i in range(1, len(df)):
            curr_bar = df.iloc[i]
            curr_date = df.index[i]
            atr = curr_bar["atr14"]

            # 1. Bekleyen Limit Emri Kontrol Et
            if not in_trade and pending_order is not None:
                if i > pending_order["expire_idx"]:
                    pending_order = None # Tren kaçtı, limit iptal!
                else:
                    dir = pending_order["direction"]
                    limit_p = pending_order["limit_price"]
                    sl_p = pending_order["sl_price"]

                    # Fiyat limit seviyemize geri çekildi mi?
                    filled = False
                    if dir == "LONG" and curr_bar["low"] <= limit_p:
                        entry_p = limit_p
                        filled = True
                    elif dir == "SHORT" and curr_bar["high"] >= limit_p:
                        entry_p = limit_p
                        filled = True

                    if filled:
                        in_trade = True
                        risk_dist = abs(entry_p - sl_p)
                        curr_trade = {
                            "entry_time": curr_date,
                            "direction": dir,
                            "entry_price": entry_p,
                            "initial_sl": sl_p,
                            "current_sl": sl_p,
                            "risk_distance": risk_dist,
                            "risk_dollars": 50.0,
                            "max_extreme": entry_p,
                            "partial_taken": False,
                            "realized_r": 0.0,
                            "remaining_ratio": 1.0
                        }
                        pending_order = None

            # 2. Aktif Pozisyonu Yönet (2.0R TP + 2.5x ATR Trailing)
            if in_trade and curr_trade is not None:
                dir = curr_trade["direction"]
                entry = curr_trade["entry_price"]
                sl = curr_trade["current_sl"]
                risk_dist = curr_trade["risk_distance"]

                if dir == "LONG":
                    max_h = max(curr_trade["max_extreme"], curr_bar["high"])
                    curr_trade["max_extreme"] = max_h
                    peak_r = (max_h - entry) / risk_dist

                    # Kâr kilitleme
                    if peak_r >= 2.0 and not curr_trade["partial_taken"]:
                        curr_trade["partial_taken"] = True
                        curr_trade["realized_r"] = 1.0 # 0.5 * 2.0 = +1.0R cepte!
                        curr_trade["remaining_ratio"] = 0.50
                        sl = max(sl, entry + (0.05 * risk_dist))
                        curr_trade["current_sl"] = sl

                    # Trailing Stop (Kalan %50)
                    if curr_trade["partial_taken"]:
                        trail_sl = max_h - (2.5 * atr)
                        if trail_sl > sl:
                            sl = trail_sl
                            curr_trade["current_sl"] = sl

                    if curr_bar["low"] <= sl:
                        exit_p = min(curr_bar["open"], sl)
                        runner_r = (exit_p - entry) / risk_dist
                        if curr_trade["partial_taken"]:
                            total_r = round(curr_trade["realized_r"] + (curr_trade["remaining_ratio"] * runner_r), 2)
                            reason = "Trailing Runner TP"
                        else:
                            total_r = round(runner_r, 2)
                            reason = "Stop Loss"

                        holding = (curr_date - curr_trade["entry_time"]).days
                        trades.append(ConvexityTrade(
                            entry_date=str(curr_trade["entry_time"].date()),
                            exit_date=str(curr_date.date()),
                            symbol=symbol,
                            direction=dir,
                            entry_price=entry,
                            initial_sl=curr_trade["initial_sl"],
                            exit_price=exit_p,
                            holding_days=holding,
                            risk_dollars=50.0,
                            pnl_dollars=total_r * 50.0,
                            pnl_r=total_r,
                            exit_reason=reason,
                            peak_r=round(peak_r, 2),
                            partial_taken=curr_trade["partial_taken"]
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
                        if curr_trade["partial_taken"]:
                            total_r = round(curr_trade["realized_r"] + (curr_trade["remaining_ratio"] * runner_r), 2)
                            reason = "Trailing Runner TP"
                        else:
                            total_r = round(runner_r, 2)
                            reason = "Stop Loss"

                        holding = (curr_date - curr_trade["entry_time"]).days
                        trades.append(ConvexityTrade(
                            entry_date=str(curr_trade["entry_time"].date()),
                            exit_date=str(curr_date.date()),
                            symbol=symbol,
                            direction=dir,
                            entry_price=entry,
                            initial_sl=curr_trade["initial_sl"],
                            exit_price=exit_p,
                            holding_days=holding,
                            risk_dollars=50.0,
                            pnl_dollars=total_r * 50.0,
                            pnl_r=total_r,
                            exit_reason=reason,
                            peak_r=round(peak_r, 2),
                            partial_taken=curr_trade["partial_taken"]
                        ))
                        in_trade = False
                        curr_trade = None

            # 3. Yeni Kırılım Sinyali Taraması
            if not in_trade and pending_order is None:
                sig = detector.check_breakout(df, i, symbol)
                if sig:
                    # Sniper Kuralı: Kırılan Donchian seviyesine limit emir koy!
                    # Stopu 1.5x ATR yerine sadece 0.8x ATR tut (dar stop).
                    limit_price = sig.entry_price
                    sl_dist = 0.8 * atr
                    sl_price = limit_price - sl_dist if sig.direction == "LONG" else limit_price + sl_dist

                    pending_order = {
                        "direction": sig.direction,
                        "limit_price": limit_price,
                        "sl_price": sl_price,
                        "expire_idx": i + 3 # 3 gün içinde retest gelmezse iptal
                    }

        return trades

    # Model B'yi Çalıştır
    trades_sniper = []
    for sym, df in market_data.items():
        t = simulate_sniper_retest(df, sym)
        trades_sniper.extend(t)

    rep_sniper = PerformanceReporter.calculate(trades_sniper, name="Begonya Sniper Retest (0.8x ATR Dar SL)")

    # =========================================================================
    # MODEL C: BEGONYA DISPLACEMENT & TIGHT SL
    # Kırılım anında güçlü gövdeli mum varsa hemen gir, ancak stopu kırılım mumunun
    # dibine (ortalama 0.9x ATR) koy. Hiçbir fırsatı kaçırma ama stopu dar tut!
    # =========================================================================
    def simulate_displacement_tight(df: pd.DataFrame, symbol: str) -> List[ConvexityTrade]:
        trades = []
        in_trade = False
        curr_trade = None

        for i in range(1, len(df)):
            curr_bar = df.iloc[i]
            curr_date = df.index[i]
            atr = curr_bar["atr14"]

            if in_trade and curr_trade is not None:
                dir = curr_trade["direction"]
                entry = curr_trade["entry_price"]
                sl = curr_trade["current_sl"]
                risk_dist = curr_trade["risk_distance"]

                if dir == "LONG":
                    max_h = max(curr_trade["max_extreme"], curr_bar["high"])
                    curr_trade["max_extreme"] = max_h
                    peak_r = (max_h - entry) / risk_dist

                    if peak_r >= 2.0 and not curr_trade["partial_taken"]:
                        curr_trade["partial_taken"] = True
                        curr_trade["realized_r"] = 1.0
                        curr_trade["remaining_ratio"] = 0.50
                        sl = max(sl, entry + (0.05 * risk_dist))
                        curr_trade["current_sl"] = sl

                    if curr_trade["partial_taken"]:
                        trail_sl = max_h - (2.5 * atr)
                        if trail_sl > sl:
                            sl = trail_sl
                            curr_trade["current_sl"] = sl

                    if curr_bar["low"] <= sl:
                        exit_p = min(curr_bar["open"], sl)
                        runner_r = (exit_p - entry) / risk_dist
                        total_r = round(curr_trade["realized_r"] + (curr_trade["remaining_ratio"] * runner_r), 2) if curr_trade["partial_taken"] else round(runner_r, 2)
                        trades.append(ConvexityTrade(
                            entry_date=str(curr_trade["entry_time"].date()), exit_date=str(curr_date.date()),
                            symbol=symbol, direction=dir, entry_price=entry, initial_sl=curr_trade["initial_sl"],
                            exit_price=exit_p, holding_days=(curr_date - curr_trade["entry_time"]).days,
                            risk_dollars=50.0, pnl_dollars=total_r * 50.0, pnl_r=total_r, exit_reason="Exit",
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
                        total_r = round(curr_trade["realized_r"] + (curr_trade["remaining_ratio"] * runner_r), 2) if curr_trade["partial_taken"] else round(runner_r, 2)
                        trades.append(ConvexityTrade(
                            entry_date=str(curr_trade["entry_time"].date()), exit_date=str(curr_date.date()),
                            symbol=symbol, direction=dir, entry_price=entry, initial_sl=curr_trade["initial_sl"],
                            exit_price=exit_p, holding_days=(curr_date - curr_trade["entry_time"]).days,
                            risk_dollars=50.0, pnl_dollars=total_r * 50.0, pnl_r=total_r, exit_reason="Exit",
                            peak_r=round(peak_r, 2), partial_taken=curr_trade["partial_taken"]
                        ))
                        in_trade = False
                        curr_trade = None

            if not in_trade:
                sig = detector.check_breakout(df, i, symbol)
                if sig:
                    # Displacement Kontrolü: Mum gövdesi ATR'nin en az %40'ı olmalı
                    body = abs(curr_bar["close"] - curr_bar["open"])
                    if body >= 0.40 * atr:
                        # Dar Stop: Kırılım mumunun dibi / tepesi (veya max 0.9x ATR)
                        if sig.direction == "LONG":
                            sl_p = max(curr_bar["low"] - (0.1 * atr), sig.entry_price - (0.9 * atr))
                        else:
                            sl_p = min(curr_bar["high"] + (0.1 * atr), sig.entry_price + (0.9 * atr))

                        in_trade = True
                        risk_dist = abs(sig.entry_price - sl_p)
                        curr_trade = {
                            "entry_time": curr_date,
                            "direction": sig.direction,
                            "entry_price": sig.entry_price,
                            "initial_sl": sl_p,
                            "current_sl": sl_p,
                            "risk_distance": risk_dist,
                            "risk_dollars": 50.0,
                            "max_extreme": sig.entry_price,
                            "partial_taken": False,
                            "realized_r": 0.0,
                            "remaining_ratio": 1.0
                        }

        return trades

    trades_displacement = []
    for sym, df in market_data.items():
        t = simulate_displacement_tight(df, sym)
        trades_displacement.extend(t)

    rep_displacement = PerformanceReporter.calculate(trades_displacement, name="Begonya Displacement + Dar SL (0.9x ATR)")

    # =========================================================================
    # KARŞILAŞTIRMA RAPORU
    # =========================================================================
    sep = "═" * 105
    mid = "─" * 105
    print(sep)
    print("  📊 5 YILLIK SİMÜLASYON SONUÇLARI: BAZ MODEL vs. BEGONYA SNIPER MODELLERİ 📊  ")
    print(sep)
    print(f"{'Metrik':<32} │ {'Baz Model (Kör Market)':<22} │ {'Model B (Sniper Retest)':<22} │ {'Model C (Displacement)'}")
    print(mid)
    print(f"{'Toplam İşlem Sayısı (N)':<32} │ {rep_baseline.total_trades:>6} işlem             │ {rep_sniper.total_trades:>6} işlem             │ {rep_displacement.total_trades:>6} işlem")
    print(f"{'Kazanma Oranı (Win Rate)':<32} │ %{rep_baseline.win_rate_pct:>5.1f}                 │ %{rep_sniper.win_rate_pct:>5.1f}                 │ %{rep_displacement.win_rate_pct:>5.1f}")
    print(f"{'Ortalama Win / Loss':<32} │ +{rep_baseline.avg_win_r:.2f}R / -{rep_baseline.avg_loss_r:.2f}R       │ +{rep_sniper.avg_win_r:.2f}R / -{rep_sniper.avg_loss_r:.2f}R       │ +{rep_displacement.avg_win_r:.2f}R / -{rep_displacement.avg_loss_r:.2f}R")
    print(f"{'Asimetrik Oran (Payoff)':<32} │ {rep_baseline.payoff_ratio:>5.2f}x                │ {rep_sniper.payoff_ratio:>5.2f}x                │ {rep_displacement.payoff_ratio:>5.2f}x")
    print(f"{'Kâr Faktörü (Profit Factor)':<32} │ {rep_baseline.profit_factor:>5.2f}                 │ {rep_sniper.profit_factor:>5.2f}                 │ {rep_displacement.profit_factor:>5.2f}")
    print(f"{'Matematiksel Beklenti (E)':<32} │ {rep_baseline.expectancy_r:>+5.2f} R / işlem        │ {rep_sniper.expectancy_r:>+5.2f} R / işlem        │ {rep_displacement.expectancy_r:>+5.2f} R / işlem")
    print(f"{'Toplam Net Getiri (R)':<32} │ {rep_baseline.total_r:>+6.2f} R               │ {rep_sniper.total_r:>+6.2f} R               │ {rep_displacement.total_r:>+6.2f} R")
    print(f"{'Max Drawdown':<32} │ -{rep_baseline.max_drawdown_r:.1f} R (%{rep_baseline.max_drawdown_pct:.1f})      │ -{rep_sniper.max_drawdown_r:.1f} R (%{rep_sniper.max_drawdown_pct:.1f})      │ -{rep_displacement.max_drawdown_r:.1f} R (%{rep_displacement.max_drawdown_pct:.1f})")
    print(sep)

if __name__ == "__main__":
    run_sniper_experiment()
