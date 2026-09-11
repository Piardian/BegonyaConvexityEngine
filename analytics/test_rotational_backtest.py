import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict, Set, Tuple
from datetime import datetime
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
from engine.trend_detector import TrendDetector, BreakoutSignal
from engine.convexity_simulator import ConvexitySimulator, ConvexityTrade
from engine.rotational_engine import RotationalSelectionEngine, AuditRecord
from analytics.performance_reporter import PerformanceReporter

logging.basicConfig(level=logging.WARNING)

def run_comparative_backtest():
    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    symbols = config.get("backtest", {}).get("symbols", [])
    initial_capital = config.get("backtest", {}).get("initial_capital", 10000.0)

    loader = MarketLoader()
    detector = TrendDetector(config)

    # 1. 16 Varlığın verilerini yükle ve göstergeleri hesapla
    market_data: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = loader.load_symbol_data(sym, period="5y")
        if df.empty or len(df) < 150:
            continue
        market_data[sym] = df

    print("\n" + "═" * 110)
    print(" 🔄 BEGONYA DİNAMİK ROTASYON & LİSTEDEN ÇIKARMA (SELECTION & EVICTION) 5 YILLIK BACKTESTİ 🔄 ")
    print("   • Evren            : 16 Küresel Varlık (Altın, Gümüş, Ham Petrol, Nasdaq, S&P, BTC, ETH, SOL, Majör FX)")
    print("   • Dönem            : Ekim 2021 - Eylül 2026 (5 Tam Yıl / Gerçekçi Kriz & Boğa Döngüsü)")
    print("   • Aktif Kontenjan  : Aynı Anda Maksimum 4 Varlık (Top-4 Momentum)")
    print("   • Seçim Kuralı     : 60 Günlük RS Sıralaması + 200 Günlük EMA Üstü (Trend Filtresi)")
    print("   • Çıkarma Kuralı   : Sıralama 7'nin Altına Düşüş (Histerezis) OR 200 EMA Kırılımı OR Peş Peşe 2 Stop")
    print("═" * 110 + "\n")

    # =========================================================================
    # DENEY 1: STATİK MODEL (Eski Hali - 16 Varlıkta Körlemesine İşlem)
    # =========================================================================
    sim_static = ConvexitySimulator(config)
    static_trades: List[ConvexityTrade] = []

    for sym, df in market_data.items():
        signals = []
        for i in range(1, len(df)):
            sig = detector.check_breakout(df, i, sym)
            if sig:
                signals.append(sig)
        s_trades = sim_static.simulate_trades_for_symbol(df, signals, sym)
        static_trades.extend(s_trades)

    static_trades.sort(key=lambda t: t.entry_date)
    rep_static = PerformanceReporter.calculate(static_trades, name="Statik Model (16 Varlık)", initial_capital=initial_capital)

    # =========================================================================
    # DENEY 2: DİNAMİK ROTASYON & LİSTEDEN ÇIKARMA MODELİ (Yeni Model)
    # =========================================================================
    rot_engine = RotationalSelectionEngine(config)
    
    # Ortak tarih takvimi oluştur (Tüm iş günleri)
    all_dates = sorted(list(set([d for df in market_data.values() for d in df.index])))
    # 2021-11-01'den itibaren başlat (Warmup tamamlandıktan sonra)
    sim_dates = [d for d in all_dates if d >= pd.Timestamp("2021-11-01", tz="UTC")]

    active_trades: Dict[str, dict] = {} # symbol -> trade_dict
    closed_rot_trades: List[ConvexityTrade] = []

    rebalance_freq_days = config.get("rotational_selection", {}).get("scan_frequency_days", 5)
    last_rebalance_idx = -999

    for day_idx, curr_date in enumerate(sim_dates):
        # 1. Haftalık / Periyodik Rebalance Kontrolü
        if day_idx - last_rebalance_idx >= rebalance_freq_days:
            last_rebalance_idx = day_idx
            active_symbols, events = rot_engine.evaluate_and_rebalance(
                current_date=curr_date,
                symbol_data_map=market_data,
                open_positions=set(active_trades.keys())
            )

            # Eğer açık işlemde olan bir varlık azledildiyse (EVICTED) zarif tasfiye moduna al
            for sym, tr in active_trades.items():
                if sym not in active_symbols:
                    tr["graceful_exit"] = True

        # 2. Açık İşlemleri Güncelle ve Kapat
        symbols_to_close = []
        for sym, tr in active_trades.items():
            df = market_data[sym]
            if curr_date not in df.index:
                continue
            bar = df.loc[curr_date]
            entry = tr["entry_price"]
            risk_dist = tr["risk_distance"]
            sl = tr["current_sl"]
            atr = bar["atr14"]
            direction = tr["direction"]
            is_graceful = tr.get("graceful_exit", False)

            # R Zirvesi
            if direction == "LONG":
                high = bar["high"]
                low = bar["low"]
                close = bar["close"]
                tr["max_extreme"] = max(tr["max_extreme"], high)
                peak_r = (tr["max_extreme"] - entry) / risk_dist

                # 2.0R Kısmi Kâr Alma
                if not tr["partial_taken"] and peak_r >= 2.0:
                    tr["partial_taken"] = True
                    tr["current_sl"] = entry + (0.5 * risk_dist)  # Kârı kilitle

                # İzleyen Stop (Trailing Stop)
                # Eğer Graceful Exit modundaysa daha dar stop (1.0x ATR)
                trailing_mult = 1.0 if is_graceful else 2.0
                trailing_sl = tr["max_extreme"] - (trailing_mult * atr)

                if tr["partial_taken"] or is_graceful:
                    tr["current_sl"] = max(tr["current_sl"], trailing_sl)

                # Stop Oldu mu?
                if low <= tr["current_sl"]:
                    exit_price = min(bar["open"], tr["current_sl"]) if bar["open"] < tr["current_sl"] else tr["current_sl"]
                    exit_reason = "GRACEFUL_EVICTION_EXIT" if is_graceful else ("TRAILING_STOP" if tr["partial_taken"] else "STOP_LOSS")
                    
                    # PnL hesapla
                    if tr["partial_taken"]:
                        first_half_r = 2.0
                        second_half_r = (exit_price - entry) / risk_dist
                        final_pnl_r = 0.5 * first_half_r + 0.5 * second_half_r
                    else:
                        final_pnl_r = (exit_price - entry) / risk_dist

                    # Slipaj cezası
                    final_pnl_r -= 0.05
                    pnl_usd = final_pnl_r * 50.0  # %0.5 risk = $50

                    # Eğer zarar ettiyse motora bildir (Karantina takibi)
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
                    closed_rot_trades.append(closed_trade)
                    symbols_to_close.append(sym)

        for sym in symbols_to_close:
            del active_trades[sym]

        # 3. Yeni Kırılım Sinyallerini Tara (YALNIZCA AKTİF KONTENJANDAKİLER)
        for sym in rot_engine.active_universe:
            # Zaten pozisyonda ise yeni işlem açma
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
                # İşleme Gir!
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

    rep_rot = PerformanceReporter.calculate(closed_rot_trades, name="Dinamik Rotasyon Modeli (Top-4)", initial_capital=initial_capital)

    # Max Drawdown R hesabı
    def calc_max_dd_r(trades):
        eq, peak, m_dd = 0.0, 0.0, 0.0
        for t in trades:
            eq += t.pnl_r
            if eq > peak: peak = eq
            if (peak - eq) > m_dd: m_dd = peak - eq
        return m_dd

    mdd_static = calc_max_dd_r(static_trades)
    mdd_rot = calc_max_dd_r(closed_rot_trades)

    # Bileşik %1.5 Risk Model Simülasyonu
    def calc_compounding(trades, risk_pct=0.015):
        cap = 10000.0
        peak = 10000.0
        mdd = 0.0
        for t in trades:
            r_usd = cap * risk_pct
            cap += t.pnl_r * r_usd
            if cap > peak: peak = cap
            cur_dd = (peak - cap) / peak * 100
            if cur_dd > mdd: mdd = cur_dd
        return cap, (cap - 10000.0) / 100.0, mdd

    c_static_cap, c_static_ret, c_static_dd = calc_compounding(static_trades)
    c_rot_cap, c_rot_ret, c_rot_dd = calc_compounding(closed_rot_trades)

    # =========================================================================
    # RAPORLAMA BÖLÜMÜ
    # =========================================================================
    sep = "═" * 110
    mid = "─" * 110

    print("📋 [BÖLÜM 1] DİNAMİK ROTASYON AUDIT GÜNLÜĞÜ (KİMLER GİRDİ, KİMLER ATILDI?)")
    print(sep)
    print(f"{'Tarih':<11} │ {'Varlık':<8} │ {'Eylem':<10} │ {'Sıra':<5} │ {'RS %':<7} │ {'Gerekçe / Kural':<50}")
    print(mid)

    # Örnek ve kritik olayları listele (tarih sırasına göre)
    sample_audit = rot_engine.audit_log[:40]  # İlk 40 kritik olay
    for a in sample_audit:
        action_icon = "🟢 ALINDI" if a.action == "ADMITTED" else ("🔴 ATILDI" if a.action == "EVICTED" else "🟡 KARANTİNA")
        print(f"{a.date:<11} │ {a.symbol:<8} │ {action_icon:<10} │ #{a.rank:<4} │ %{a.rs_score:<6.1f} │ {a.reason[:50]:<50}")

    if len(rot_engine.audit_log) > 40:
        print(f"  ... [Toplam {len(rot_engine.audit_log)} Adet Listeye Alım/Çıkarım Olayı Gerçekleşti] ...")

    # Çıkarılma Nedenleri İstatistiği
    evictions = [a for a in rot_engine.audit_log if a.action == "EVICTED"]
    reasons_count = {}
    for e in evictions:
        key = "200 EMA Kırılımı (Trend Ölümü)" if "200 EMA" in e.reason else (
              "Sıralama Düşüşü (RS Decay)" if "Sıralama" in e.reason else (
              "Peş Peşe Stop (Karantina)" if "Karantina" in e.reason else "Volatilite Sönmesi"))
        reasons_count[key] = reasons_count.get(key, 0) + 1

    print("\n" + sep)
    print("🚨 [BÖLÜM 2] LİSTEDEN ÇIKARILMA (KIRMIZI KART) İSTATİSTİKLERİ")
    print(sep)
    for r_name, cnt in reasons_count.items():
        print(f"  • {r_name:<40}: {cnt:>3} kez kırmızı kart gösterildi.")

    print("\n" + sep)
    print("📊 [BÖLÜM 3] STATİK MODEL vs. DİNAMİK ROTASYON KARŞILAŞTIRMA KARNESİ")
    print(sep)
    print(f"{'Performans Metriği':<35} │ {'Statik Model (16 Varlık Kör)':<32} │ {'Dinamik Rotasyon (Top-4 Eviction)':<32}")
    print(mid)
    print(f"{'Toplam İşlem Sayısı':<35} │ {rep_static.total_trades:<32} │ {rep_rot.total_trades:<32}")
    print(f"{'Kazanma Oranı (Win Rate)':<35} │ %{rep_static.win_rate_pct:<31.1f} │ %{rep_rot.win_rate_pct:<31.1f}")
    print(f"{'Kâr Faktörü (Profit Factor)':<35} │ {rep_static.profit_factor:<32.2f} │ {rep_rot.profit_factor:<32.2f}")
    print(f"{'Ortalama Kâr/Zarar (Payoff Ratio)':<35} │ {rep_static.payoff_ratio:<32.2f} │ {rep_rot.payoff_ratio:<32.2f}")
    print(f"{'Toplam Net Getiri (R-Cinsi)':<35} │ {rep_static.total_r:>+7.2f} R{'':<23} │ {rep_rot.total_r:>+7.2f} R{'':<23}")
    print(f"{'Maksimum Çekilme (Max Drawdown R)':<35} │ -{mdd_static:.2f} R{'':<25} │ -{mdd_rot:.2f} R{'':<25}")
    print(mid)
    print(f"{'Sermaye Getirisi (%0.5 Sabit Risk)':<35} │ %{rep_static.return_on_capital_pct:>+6.2f} (+${rep_static.total_pnl_dollars:,.2f}){'':<7} │ %{rep_rot.return_on_capital_pct:>+6.2f} (+${rep_rot.total_pnl_dollars:,.2f}){'':<7}")
    print(f"{'Maksimum Sermaye Çekilmesi (Max DD)':<35} │ %{rep_static.max_drawdown_pct:.2f}{'':<27} │ %{rep_rot.max_drawdown_pct:.2f}{'':<27}")
    print(mid)
    print(f"{'🚀 %1.5 BİLEŞİK RİSKLİ FİNAL SERMAYE':<35} │ ${c_static_cap:,.2f} (+%{c_static_ret:.1f}){'':<7} │ ${c_rot_cap:,.2f} (+%{c_rot_ret:.1f}){'':<7}")
    print(f"{'🚀 %1.5 BİLEŞİKTE MAKSİMUM ÇEKİLME':<35} │ %{c_static_dd:.2f}{'':<27} │ %{c_rot_dd:.2f}{'':<27}")
    print(sep + "\n")

if __name__ == "__main__":
    run_comparative_backtest()
