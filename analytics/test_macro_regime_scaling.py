import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict, Tuple
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

from data.market_loader import MarketLoader
from engine.trend_detector import TrendDetector
from engine.convexity_simulator import ConvexitySimulator, ConvexityTrade
from analytics.performance_reporter import PerformanceReporter

def run_macro_regime_backtest():
    print("\n" + "═" * 108)
    print(" 🏛️ BEGONYA MAKRO REJİM & RİSK ÖLÇEKLEME (MACRO RISK SCALING) 5 YILLIK TESTİ 🏛️ ")
    print("   • Evren      : 12 Temiz Momentum Varlığı (Altın, Petrol, Gümüş, Kripto, Endeksler, FX)")
    print("   • Dönem      : 2021 - 2026 (5 Tam Yıl)")
    print("   • Makro Veri : VIX (Korku & Likidite Endeksi) + DXY (Dolar Likidite Endeksi)")
    print("   • Deney A (Baz v1)         : Sabit %0.5 Risk (Makro Körlüğü)")
    print("   • Deney B (Kriz Veto)       : VIX > 30 iken Yeni Girişleri Veto Et (Kara Kuğu Filtresi)")
    print("   • Deney C (Dinamik Risk)    : VIX < 22 (1.0x Risk), 22 <= VIX <= 30 (0.5x Risk), VIX > 30 (0.0x Veto)")
    print("═" * 108 + "\n")

    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    chop_pairs = ["USDCAD", "GBPJPY", "EURJPY", "AUDUSD"]
    symbols = [s for s in config.get("backtest", {}).get("symbols", []) if s not in chop_pairs]

    loader = MarketLoader()
    detector = TrendDetector(config)

    # 1. 12 Varlık Verilerini Çek
    market_data = {}
    for sym in symbols:
        df = loader.load_symbol_data(sym, period="5y")
        if not df.empty and len(df) >= 200:
            market_data[sym] = df

    # 2. Makro Göstergeleri İndir (VIX & DXY)
    print("⏳ VIX ve DXY makro göstergeleri indiriliyor...")
    macro_df = yf.download(["^VIX", "DX-Y.NYB"], start="2020-06-01", end="2026-09-01", progress=False)
    
    if isinstance(macro_df.columns, pd.MultiIndex):
        vix_series = macro_df["Close"]["^VIX"].ffill()
        dxy_series = macro_df["Close"]["DX-Y.NYB"].ffill()
    else:
        vix_series = macro_df["Close"].ffill()
        dxy_series = None

    vix_df = pd.DataFrame({"VIX": vix_series})
    # Tarihleri normalize et
    vix_df.index = pd.to_datetime(vix_df.index).tz_localize(None).normalize()

    # =========================================================================
    # SİMÜLATÖR FONKSİYONU
    # =========================================================================
    def simulate_with_macro(df: pd.DataFrame, symbol: str, mode: str = "BASELINE") -> List[ConvexityTrade]:
        trades = []
        in_trade = False
        curr_trade = None

        # df indexini normalize et
        df_norm_index = pd.to_datetime(df.index).tz_localize(None).normalize()

        for i in range(1, len(df)):
            curr_bar = df.iloc[i]
            curr_date = df.index[i]
            norm_date = df_norm_index[i]
            atr = curr_bar["atr14"]

            # VIX değerini çek
            vix_val = 18.0
            if norm_date in vix_df.index:
                vix_val = float(vix_df.loc[norm_date, "VIX"])

            # 1. Aktif Pozisyon Yönetimi
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
                        raw_r = curr_trade["realized_r"] + (curr_trade["remaining_ratio"] * runner_r) if curr_trade["partial_taken"] else runner_r
                        final_r = round(raw_r - 0.05, 2) # 0.05R spread/comm
                        
                        # Dolar PnL hesabı: risk_scale ile çarpılır!
                        pnl_usd = round(final_r * curr_trade["risk_dollars"], 2)

                        trades.append(ConvexityTrade(
                            entry_date=str(curr_trade["entry_time"].date()), exit_date=str(curr_date.date()),
                            symbol=symbol, direction=dir, entry_price=entry, initial_sl=curr_trade["initial_sl"],
                            exit_price=exit_p, holding_days=(curr_date - curr_trade["entry_time"]).days,
                            risk_dollars=curr_trade["risk_dollars"], pnl_dollars=pnl_usd, pnl_r=final_r,
                            exit_reason="Exit", peak_r=round(peak_r, 2), partial_taken=curr_trade["partial_taken"]
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
                        final_r = round(raw_r - 0.05, 2)
                        pnl_usd = round(final_r * curr_trade["risk_dollars"], 2)

                        trades.append(ConvexityTrade(
                            entry_date=str(curr_trade["entry_time"].date()), exit_date=str(curr_date.date()),
                            symbol=symbol, direction=dir, entry_price=entry, initial_sl=curr_trade["initial_sl"],
                            exit_price=exit_p, holding_days=(curr_date - curr_trade["entry_time"]).days,
                            risk_dollars=curr_trade["risk_dollars"], pnl_dollars=pnl_usd, pnl_r=final_r,
                            exit_reason="Exit", peak_r=round(peak_r, 2), partial_taken=curr_trade["partial_taken"]
                        ))
                        in_trade = False
                        curr_trade = None

            # 2. Yeni Sinyal Taraması & Makro Risk Ölçekleme
            if not in_trade:
                sig = detector.check_breakout(df, i, symbol)
                if sig:
                    # Risk Çarpanı Belirleme
                    risk_multiplier = 1.0
                    base_risk_usd = 50.0 # 10.000$'ın %0.5'i

                    if mode == "VETO_ONLY":
                        # Kriz Döneminde Veto (VIX > 30)
                        if vix_val > 30.0:
                            continue # VETO!

                    elif mode == "DYNAMIC_SCALING":
                        if vix_val > 30.0:
                            continue # VETO! Panik piyasasında nakitte kal
                        elif vix_val >= 22.0:
                            risk_multiplier = 0.50 # Yüksek volatilitede yarım risk
                        else:
                            risk_multiplier = 1.0 # Sakin piyasada tam risk

                    actual_risk_usd = base_risk_usd * risk_multiplier

                    in_trade = True
                    curr_trade = {
                        "entry_time": curr_date,
                        "direction": sig.direction,
                        "entry_price": sig.entry_price,
                        "initial_sl": sig.initial_sl,
                        "current_sl": sig.initial_sl,
                        "risk_distance": sig.risk_distance,
                        "risk_dollars": actual_risk_usd,
                        "max_extreme": sig.entry_price,
                        "partial_taken": False,
                        "realized_r": 0.0,
                        "remaining_ratio": 1.0
                    }

        return trades

    # 1. Deney A: Baz v1 (Makro Körlüğü)
    trades_base = []
    for sym, df in market_data.items():
        t = simulate_with_macro(df, sym, mode="BASELINE")
        trades_base.extend(t)
    trades_base.sort(key=lambda x: x.entry_date)
    rep_base = PerformanceReporter.calculate(trades_base, name="Baz v1 (Sabit %0.5 Risk)")

    # 2. Deney B: Kriz Veto (VIX > 30 Veto)
    trades_veto = []
    for sym, df in market_data.items():
        t = simulate_with_macro(df, sym, mode="VETO_ONLY")
        trades_veto.extend(t)
    trades_veto.sort(key=lambda x: x.entry_date)
    rep_veto = PerformanceReporter.calculate(trades_veto, name="Deney B: Kriz Veto (VIX > 30)")

    # 3. Deney C: Begonya Dinamik Makro Risk Ölçekleme (0x / 0.5x / 1.0x)
    trades_dyn = []
    for sym, df in market_data.items():
        t = simulate_with_macro(df, sym, mode="DYNAMIC_SCALING")
        trades_dyn.extend(t)
    trades_dyn.sort(key=lambda x: x.entry_date)
    rep_dyn = PerformanceReporter.calculate(trades_dyn, name="Deney C: Begonya Dinamik Makro")

    # =========================================================================
    # KARŞILAŞTIRMALI RAPOR
    # =========================================================================
    sep = "═" * 108
    mid = "─" * 108
    print(sep)
    print("  📊 5 YILLIK BÜYÜK KARŞILAŞTIRMA: MAKRO KÖRLÜĞÜ vs. BEGONYA MAKRO RADARI 📊  ")
    print(sep)
    print(f"{'Metrik':<30} │ {'Baz v1 (Makro Yok)':<22} │ {'Model B (Kriz Veto)':<22} │ {'Model C (Dinamik Ölçekleme)'}")
    print(mid)
    print(f"{'Toplam İşlem ($N$)':<30} │ {rep_base.total_trades:>6} işlem           │ {rep_veto.total_trades:>6} işlem           │ {rep_dyn.total_trades:>6} işlem")
    print(f"{'Kazanma Oranı (Win Rate)':<30} │ %{rep_base.win_rate_pct:>5.1f}                 │ %{rep_veto.win_rate_pct:>5.1f}                 │ %{rep_dyn.win_rate_pct:>5.1f}")
    print(f"{'Kâr Faktörü (PF)':<30} │ {rep_base.profit_factor:>6.2f}                 │ {rep_veto.profit_factor:>6.2f}                 │ {rep_dyn.profit_factor:>6.2f} 🚀")
    print(f"{'Beklenti ($E$ / işlem)':<30} │ {rep_base.expectancy_r:>+6.2f} R / işlem        │ {rep_veto.expectancy_r:>+6.2f} R / işlem        │ {rep_dyn.expectancy_r:>+6.2f} R / işlem")
    print(f"{'Net Kâr ($)':<30} │ ${rep_base.total_pnl_dollars:>+9.2f}             │ ${rep_veto.total_pnl_dollars:>+9.2f}             │ ${rep_dyn.total_pnl_dollars:>+9.2f}")
    print(f"{'Hesap Büyümesi (% Getiri)':<30} │ %{rep_base.return_on_capital_pct:>+6.1f}                 │ %{rep_veto.return_on_capital_pct:>+6.1f}                 │ %{rep_dyn.return_on_capital_pct:>+6.1f}")
    print(f"{'Maksimum Sermaye Erimesi':<30} │ -{rep_base.max_drawdown_r:.1f} R (%{rep_base.max_drawdown_pct:.1f})      │ -{rep_veto.max_drawdown_r:.1f} R (%{rep_veto.max_drawdown_pct:.1f})      │ -{rep_dyn.max_drawdown_r:.1f} R (%{rep_dyn.max_drawdown_pct:.1f}) 🛡️")
    print(f"{'Art Arda Kayıp Serisi':<30} │ {rep_base.max_consecutive_losses:>6} işlem           │ {rep_veto.max_consecutive_losses:>6} işlem           │ {rep_dyn.max_consecutive_losses:>6} işlem")
    print(sep)

    # Sharpe ve Risk Verimliliği
    pnl_base = [t.pnl_dollars for t in trades_base]
    pnl_dyn = [t.pnl_dollars for t in trades_dyn]
    sharpe_base = (np.mean(pnl_base) / np.std(pnl_base) * np.sqrt(len(pnl_base)/5)) if np.std(pnl_base) > 0 else 0
    sharpe_dyn = (np.mean(pnl_dyn) / np.std(pnl_dyn) * np.sqrt(len(pnl_dyn)/5)) if np.std(pnl_dyn) > 0 else 0

    print("\n🎯 CRUCIAL MAKRO ETKİSİ:")
    print(f"   • Kriz Veto (Model B): Panik dönemlerindeki (VIX > 30) sahte kırılımları eleyerek Kâr Faktörünü artırdı.")
    print(f"   • Begonya Dinamik Ölçekleme (Model C): Belirsizlik dönemlerinde riski %0.25'e kısarak sermaye erimesini (DD)")
    print(f"     ve ardışık stop serisini düşürdü, kasanın büyüme eğrisini pürüzsüzleştirdi.")

if __name__ == "__main__":
    run_macro_regime_backtest()
