import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict, Set
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
from engine.rotational_engine import RotationalSelectionEngine, AuditRecord
from analytics.performance_reporter import PerformanceReporter

logging.basicConfig(level=logging.WARNING)

def run_2026_rotational_backtest():
    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    symbols = config.get("backtest", {}).get("symbols", [])
    initial_capital = config.get("backtest", {}).get("initial_capital", 10000.0)

    loader = MarketLoader()
    detector = TrendDetector(config)

    market_data: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = loader.load_symbol_data(sym, period="5y")
        if not df.empty and len(df) >= 150:
            market_data[sym] = df

    print("\n" + "═" * 110)
    print(" 🎯 BEGONYA DİNAMİK ROTASYON & LİSTEDEN ÇIKARMA │ 2026 YILI ÖZEL BACKTEST KARNESİ 🎯 ")
    print("   • Evren          : 16 Küresel Varlık (Altın, Gümüş, Petrol, Nasdaq, S&P, BTC, ETH, SOL, FX)")
    print("   • Test Dönemi    : 01 Ocak 2026 - 10 Eylül 2026 (2026 YTD - 8.5 Ay)")
    print("   • Portföy Limiti : Aynı Anda Maksimum 4 Varlık (Top-4 Momentum)")
    print("   • Seçim Kuralı   : 60 Günlük RS Sıralaması + 200 EMA Üstü")
    print("   • Çıkarma Kuralı : Sıralama >= 7 (Histerezis) OR 200 EMA Kırılımı OR Peş Peşe 2 Stop (Karantina)")
    print("═" * 110 + "\n")

    # =========================================================================
    # 1. STATİK MODEL (2026 Yılı - 16 Varlık Kör Giriş)
    # =========================================================================
    sim_static = ConvexitySimulator(config)
    static_2026_trades: List[ConvexityTrade] = []

    for sym, df in market_data.items():
        signals = []
        for i in range(1, len(df)):
            if df.index[i] >= pd.Timestamp("2026-01-01", tz="UTC"):
                sig = detector.check_breakout(df, i, sym)
                if sig:
                    signals.append(sig)
        s_trades = sim_static.simulate_trades_for_symbol(df, signals, sym)
        s_26 = [t for t in s_trades if t.entry_date >= "2026-01-01"]
        static_2026_trades.extend(s_26)

    static_2026_trades.sort(key=lambda t: t.entry_date)
    rep_static = PerformanceReporter.calculate(static_2026_trades, name="2026 Statik Model", initial_capital=initial_capital)

    # =========================================================================
    # 2. DİNAMİK ROTASYON MODELİ (2026 Yılı - Top-4 & Eviction)
    # =========================================================================
    rot_engine = RotationalSelectionEngine(config)
    all_dates = sorted(list(set([d for df in market_data.values() for d in df.index])))
    sim_dates_2026 = [d for d in all_dates if d >= pd.Timestamp("2026-01-01", tz="UTC")]

    active_trades: Dict[str, dict] = {}
    closed_trades: List[ConvexityTrade] = []

    rebalance_freq_days = config.get("rotational_selection", {}).get("scan_frequency_days", 5)
    last_rebalance_idx = -999

    for day_idx, curr_date in enumerate(sim_dates_2026):
        if day_idx - last_rebalance_idx >= rebalance_freq_days:
            last_rebalance_idx = day_idx
            active_symbols, events = rot_engine.evaluate_and_rebalance(
                current_date=curr_date,
                symbol_data_map=market_data,
                open_positions=set(active_trades.keys())
            )
            for sym, tr in active_trades.items():
                if sym not in active_symbols:
                    tr["graceful_exit"] = True

        symbols_to_close = []
        for sym, tr in active_trades.items():
            df = market_data[sym]
            if curr_date not in df.index:
                continue
            bar = df.loc[curr_date]
            entry = tr["entry_price"]
            risk_dist = tr["risk_distance"]
            atr = bar["atr14"]
            direction = tr["direction"]
            is_graceful = tr.get("graceful_exit", False)

            if direction == "LONG":
                high = bar["high"]
                low = bar["low"]
                tr["max_extreme"] = max(tr["max_extreme"], high)
                peak_r = (tr["max_extreme"] - entry) / risk_dist

                if not tr["partial_taken"] and peak_r >= 2.0:
                    tr["partial_taken"] = True
                    tr["current_sl"] = entry + (0.5 * risk_dist)

                trailing_mult = 1.0 if is_graceful else 2.0
                trailing_sl = tr["max_extreme"] - (trailing_mult * atr)
                if tr["partial_taken"] or is_graceful:
                    tr["current_sl"] = max(tr["current_sl"], trailing_sl)

                if low <= tr["current_sl"]:
                    exit_price = min(bar["open"], tr["current_sl"]) if bar["open"] < tr["current_sl"] else tr["current_sl"]
                    exit_reason = "GRACEFUL_EVICTION_EXIT" if is_graceful else ("TRAILING_STOP" if tr["partial_taken"] else "STOP_LOSS")

                    if tr["partial_taken"]:
                        final_pnl_r = 0.5 * 2.0 + 0.5 * ((exit_price - entry) / risk_dist)
                    else:
                        final_pnl_r = (exit_price - entry) / risk_dist

                    final_pnl_r -= 0.05
                    pnl_usd = final_pnl_r * 50.0

                    if final_pnl_r <= 0:
                        rot_engine.record_trade_loss(sym, curr_date)

                    closed_trade = ConvexityTrade(
                        entry_date=tr["entry_time"].strftime("%Y-%m-%d"),
                        exit_date=curr_date.strftime("%Y-%m-%d"),
                        symbol=sym,
                        direction="LONG",
                        entry_price=entry,
                        initial_sl=tr["initial_sl"],
                        exit_price=exit_price,
                        holding_days=(curr_date - tr["entry_time"]).days,
                        risk_dollars=50.0,
                        pnl_dollars=round(pnl_usd, 2),
                        pnl_r=round(final_pnl_r, 2),
                        exit_reason=exit_reason,
                        peak_r=round(peak_r, 2),
                        partial_taken=tr["partial_taken"]
                    )
                    closed_trades.append(closed_trade)
                    symbols_to_close.append(sym)

        for sym in symbols_to_close:
            del active_trades[sym]

        for sym in rot_engine.active_universe:
            if sym in active_trades:
                continue
            df = market_data[sym]
            if curr_date not in df.index:
                continue
            idx = df.index.get_loc(curr_date)
            if idx < 2:
                continue
            sig = detector.check_breakout(df, idx, sym)
            if sig and sig.direction == "LONG":
                active_trades[sym] = {
                    "symbol": sym,
                    "entry_time": curr_date,
                    "direction": "LONG",
                    "entry_price": sig.entry_price,
                    "initial_sl": sig.initial_sl,
                    "current_sl": sig.initial_sl,
                    "risk_distance": sig.risk_distance,
                    "max_extreme": sig.entry_price,
                    "partial_taken": False,
                    "graceful_exit": False
                }

    closed_trades.sort(key=lambda t: t.entry_date)
    rep_rot = PerformanceReporter.calculate(closed_trades, name="2026 Dinamik Rotasyon", initial_capital=initial_capital)

    sep = "═" * 110
    mid = "─" * 110

    # =========================================================================
    # BÖLÜM 1: 2026 AUDIT GÜNLÜĞÜ (KİMLER GİRDİ, KİMLER ATILDI?)
    # =========================================================================
    print("📋 [BÖLÜM 1] 2026 YILI KRONOLOJİK AUDIT GÜNLÜĞÜ (LİSTEYE ALIM VE ÇIKARIMLAR)")
    print(sep)
    print(f"{'Tarih':<11} │ {'Varlık':<8} │ {'İşlem Türü':<12} │ {'Sıra':<5} │ {'RS %':<8} │ {'Gerekçe / Kural':<50}")
    print(mid)

    for a in rot_engine.audit_log:
        action_icon = "🟢 ALINDI" if a.action == "ADMITTED" else ("🔴 ATILDI" if a.action == "EVICTED" else "🟡 KARANTİNA")
        print(f"{a.date:<11} │ {a.symbol:<8} │ {action_icon:<12} │ #{a.rank:<4} │ %{a.rs_score:<7.1f} │ {a.reason[:50]:<50}")

    # =========================================================================
    # BÖLÜM 2: 2026 PARİTE BAZINDA PERFORMANS KARNESİ
    # =========================================================================
    print("\n" + sep)
    print("🏆 [BÖLÜM 2] 2026 YILI PARİTE BAZINDA NET KÂR / ZARAR DAĞILIMI")
    print(sep)
    print(f"{'Varlık':<9} │ {'İşlem':<6} │ {'Win %':<8} │ {'Kâr Faktörü':<12} │ {'Net R':<12} │ {'Net PnL ($)':<14} │ {'Giriş/Çıkış Sayısı'}")
    print(mid)

    # Paritelere göre topla
    traded_symbols = sorted(list(set([t.symbol for t in closed_trades] + [a.symbol for a in rot_engine.audit_log])))
    for sym in traded_symbols:
        s_tr = [t for t in closed_trades if t.symbol == sym]
        s_admits = len([a for a in rot_engine.audit_log if a.symbol == sym and a.action == "ADMITTED"])
        s_evicts = len([a for a in rot_engine.audit_log if a.symbol == sym and a.action == "EVICTED"])

        if s_tr:
            wins = [t for t in s_tr if t.pnl_r > 0]
            w_pct = (len(wins) / len(s_tr)) * 100.0
            tot_r = sum(t.pnl_r for t in s_tr)
            pnl_usd = tot_r * 50.0
            win_r = sum(t.pnl_r for t in wins)
            loss_r = abs(sum(t.pnl_r for t in s_tr if t.pnl_r <= 0))
            pf = win_r / loss_r if loss_r > 0 else (999.0 if win_r > 0 else 0.0)

            print(f"{sym:<9} │ {len(s_tr):<6} │ %{w_pct:<7.1f} │ {pf:<12.2f} │ {tot_r:>+7.2f} R    │ {pnl_usd:>+9.2f} $     │ 🟢 {s_admits} Giriş / 🔴 {s_evicts} Çıkış")
        else:
            print(f"{sym:<9} │ {'0':<6} │ {'-':<8} │ {'-':<12} │ {'0.00 R':<12} │ {'$0.00':<14} │ 🟢 {s_admits} Giriş / 🔴 {s_evicts} Çıkış (Kırılım Yok)")

    # =========================================================================
    # BÖLÜM 3: 2026 STATİK vs DİNAMİK KARŞILAŞTIRMA KARNESİ
    # =========================================================================
    def calc_max_dd_r(trades):
        eq, peak, m_dd = 0.0, 0.0, 0.0
        for t in trades:
            eq += t.pnl_r
            if eq > peak: peak = eq
            if (peak - eq) > m_dd: m_dd = peak - eq
        return m_dd

    mdd_stat = calc_max_dd_r(static_2026_trades)
    mdd_rot = calc_max_dd_r(closed_trades)

    print("\n" + sep)
    print("📊 [BÖLÜM 3] 2026 YILI STATİK MODEL vs. DİNAMİK ROTASYON MOTORU KARŞILAŞTIRMASI")
    print(sep)
    print(f"{'Metrik':<35} │ {'2026 Statik Model (Kör 16 Varlık)':<32} │ {'2026 Dinamik Model (Top-4 Eviction)':<32}")
    print(mid)
    print(f"{'Toplam İşlem Sayısı':<35} │ {rep_static.total_trades:<32} │ {rep_rot.total_trades:<32}")
    print(f"{'Kazanma Oranı (Win Rate)':<35} │ %{rep_static.win_rate_pct:<31.1f} │ %{rep_rot.win_rate_pct:<31.1f}")
    print(f"{'Kâr Faktörü (Profit Factor)':<35} │ {rep_static.profit_factor:<32.2f} │ {rep_rot.profit_factor:<32.2f}")
    print(f"{'Toplam Net Getiri (R)':<35} │ {rep_static.total_r:>+7.2f} R{'':<23} │ {rep_rot.total_r:>+7.2f} R{'':<23}")
    print(f"{'Net PnL ($) (%0.5 Risk)':<35} │ +${rep_static.total_pnl_dollars:,.2f}{'':<21} │ +${rep_rot.total_pnl_dollars:,.2f}{'':<21}")
    print(f"{'Maksimum Çekilme (Max DD R)':<35} │ -{mdd_stat:.2f} R{'':<25} │ -{mdd_rot:.2f} R{'':<25}")
    print(f"{'Maksimum Sermaye Kaybı (%0.5)':<35} │ %{rep_static.max_drawdown_pct:.2f}{'':<27} │ %{rep_rot.max_drawdown_pct:.2f}{'':<27}")
    print(mid)
    # %1.5 Konsantre Risk Modeli
    pnl_15_stat = rep_static.total_r * 150.0
    pnl_15_rot = rep_rot.total_r * 150.0
    print(f"{'🚀 %1.5 Konsantre Risk Getirisi ($)':<35} │ +${pnl_15_stat:,.2f} (+%{pnl_15_stat/100:.1f}){'':<7} │ +${pnl_15_rot:,.2f} (+%{pnl_15_rot/100:.1f}){'':<7}")
    print(sep + "\n")

if __name__ == "__main__":
    run_2026_rotational_backtest()
