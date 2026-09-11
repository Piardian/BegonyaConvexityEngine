import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict, Set
from datetime import datetime
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

from data.market_loader import TICKER_MAP
from engine.trend_detector import TrendDetector, BreakoutSignal
from engine.convexity_simulator import ConvexitySimulator, ConvexityTrade
from engine.rotational_engine import RotationalSelectionEngine, AuditRecord
from analytics.performance_reporter import PerformanceReporter

logging.basicConfig(level=logging.WARNING)

def load_or_download_12y_data(cache_dir: Path) -> Dict[str, pd.DataFrame]:
    """16 Varlığın 2014-2026 verisini önbellekten çeker veya indirir ve indikatörleri hazırlar."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    data = {}

    for sym, ticker in TICKER_MAP.items():
        cache_file = cache_dir / f"{sym}_2014_2026.csv"
        df = pd.DataFrame()

        if cache_file.exists():
            try:
                df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            except Exception:
                df = pd.DataFrame()

        if df.empty or len(df) < 500:
            print(f"  📥 İndiriliyor: {sym} ({ticker})...")
            raw = yf.download(ticker, start="2014-01-01", end="2026-09-11", interval="1d", progress=False)
            if raw.empty:
                continue

            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = [c[0].lower() for c in raw.columns]
            else:
                raw.columns = [c.lower() for c in raw.columns]

            if raw.index.tz is None:
                raw.index = raw.index.tz_localize("UTC")
            else:
                raw.index = raw.index.tz_convert("UTC")

            raw.sort_index(inplace=True)
            for col in ["open", "high", "low", "close"]:
                raw[col] = raw[col].astype(float)

            df = raw
            df.to_csv(cache_file)

        # İndikatörleri hesapla
        high = df["high"]
        low = df["low"]
        close_prev = df["close"].shift(1)
        tr = pd.concat([
            high - low,
            (high - close_prev).abs(),
            (low - close_prev).abs()
        ], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(window=14).mean()
        df["donchian_high_20"] = df["high"].rolling(window=20).max().shift(1)
        df["donchian_low_20"] = df["low"].rolling(window=20).min().shift(1)
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

        df.dropna(subset=["atr14", "donchian_high_20", "donchian_low_20", "ema200"], inplace=True)
        data[sym] = df

    return data

def run_12y_multiyear_backtest():
    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Olabildiğince çok (Genişletilmiş Evren) parametreleri
    config["rotational_selection"]["max_active_universe"] = 16
    config["rotational_selection"]["admission_rank_threshold"] = 16
    config["rotational_selection"]["eviction_rank_threshold"] = 99

    initial_capital = config.get("backtest", {}).get("initial_capital", 10000.0)
    cache_dir = PROJECT_DIR / "cache"

    print("\n" + "═" * 112)
    print(" 🏛️ BEGONYA CONVEXITY ENGINE │ 12 YILLIK TARİHSEL BÜYÜK KARNE (2015 - 2026) 🏛️ ")
    print("   • Evren            : 16 Çoklu Küresel Varlık (Altın, Gümüş, Petrol, Nasdaq, S&P, BTC, ETH, SOL, FX)")
    print("   • Test Periyodu    : 01 Ocak 2015 - 10 Eylül 2026 (Tam 12 Yıl / 2014 İndikatör Isınma)")
    print("   • Motor Mekaniği   : Genişletilmiş Evren (Top-16) + 4 Katmanlı Kırmızı Kart (Eviction)")
    print("   • Kırmızı Kartlar  : Fiyat < 200 EMA (Trend Ölümü) OR Volatilite Sıkışması OR Peş Peşe 2 Stop")
    print("═" * 112 + "\n")

    market_data = load_or_download_12y_data(cache_dir)
    detector = TrendDetector(config)
    rot_engine = RotationalSelectionEngine(config)

    all_dates = sorted(list(set([d for df in market_data.values() for d in df.index])))
    sim_dates = [d for d in all_dates if d >= pd.Timestamp("2015-01-01", tz="UTC")]

    active_trades: Dict[str, dict] = {}
    closed_trades: List[ConvexityTrade] = []

    rebalance_freq_days = 5
    last_rebalance_idx = -999

    for day_idx, curr_date in enumerate(sim_dates):
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

    # =========================================================================
    # YIL YIL AYRI AYRI KARNE HESAPLAMA
    # =========================================================================
    years = list(range(2015, 2027))
    yearly_reports = []

    sep = "═" * 112
    mid = "─" * 112

    print("📋 [BÖLÜM 1] YIL YIL 12 YILLIK PERFORMANS KARNESİ (2015 - 2026)")
    print(sep)
    print(f"{'Yıl':<7} │ {'İşlem':<6} │ {'Win %':<8} │ {'Kâr Faktörü':<12} │ {'Net Getiri (R)':<16} │ {'Net PnL ($)':<14} │ {'En Kârlı Varlık'}")
    print(mid)

    compounding_capital = 10000.0
    c_history = []

    for yr in years:
        yr_str = str(yr)
        y_trades = [t for t in closed_trades if t.entry_date.startswith(yr_str)]
        yr_label = f"{yr} (YTD)" if yr == 2026 else str(yr)

        if y_trades:
            wins = [t for t in y_trades if t.pnl_r > 0]
            win_pct = (len(wins) / len(y_trades)) * 100.0
            total_r = sum(t.pnl_r for t in y_trades)
            pnl_dollars = sum(t.pnl_dollars for t in y_trades)

            win_r = sum(t.pnl_r for t in wins)
            loss_r = abs(sum(t.pnl_r for t in y_trades if t.pnl_r <= 0))
            pf = win_r / loss_r if loss_r > 0 else (999.0 if win_r > 0 else 0.0)

            # En çok kâr getiren varlık
            sym_r = {}
            for t in y_trades:
                sym_r[t.symbol] = sym_r.get(t.symbol, 0.0) + t.pnl_r
            best_sym = max(sym_r.items(), key=lambda x: x[1])
            best_str = f"{best_sym[0]} (+{best_sym[1]:.1f}R)"

            # Yıllık bileşik hesap
            for t in y_trades:
                compounding_capital += t.pnl_r * (compounding_capital * 0.015)

            yearly_reports.append({
                "year": yr_label,
                "trades": len(y_trades),
                "win_pct": win_pct,
                "pf": pf,
                "total_r": total_r,
                "pnl_dollars": pnl_dollars,
                "best_sym": best_str,
                "comp_cap": compounding_capital
            })

            r_color = f"+{total_r:.2f} R" if total_r >= 0 else f"{total_r:.2f} R"
            pnl_color = f"+${pnl_dollars:,.2f}" if pnl_dollars >= 0 else f"-${abs(pnl_dollars):,.2f}"

            print(f"{yr_label:<7} │ {len(y_trades):<6} │ %{win_pct:<7.1f} │ {pf:<12.2f} │ {r_color:<16} │ {pnl_color:<14} │ {best_str}")
        else:
            print(f"{yr_label:<7} │ {'0':<6} │ {'-':<8} │ {'-':<12} │ {'0.00 R':<16} │ {'$0.00':<14} │ -")

    # =========================================================================
    # BÖLÜM 2: 12 YILLIK KÜMÜLATİF PERFORMANS VE SERMAYE BÜYÜMESİ
    # =========================================================================
    rep_total = PerformanceReporter.calculate(closed_trades, name="12 Yıllık Kümülatif", initial_capital=initial_capital)

    # Max Drawdown hesabı (R cinsinden)
    eq_r, peak_r, max_dd_r = 0.0, 0.0, 0.0
    for t in closed_trades:
        eq_r += t.pnl_r
        if eq_r > peak_r: peak_r = eq_r
        if (peak_r - eq_r) > max_dd_r: max_dd_r = peak_r - eq_r

    # Kırmızı Kart Dağılımı
    evictions = [a for a in rot_engine.audit_log if a.action == "EVICTED"]
    reasons_count = {}
    for e in evictions:
        key = "200 EMA Kırılımı (Trend Ölümü)" if "200 EMA" in e.reason else (
              "Volatilite Sönmesi (Chop)" if ("ATR" in e.reason or "Volatilite" in e.reason) else (
              "Peş Peşe Stop (Karantina)" if "Karantina" in e.reason else "Sıralama Kaybı"))
        reasons_count[key] = reasons_count.get(key, 0) + 1

    print("\n" + sep)
    print("🏆 [BÖLÜM 2] 12 YILLIK KÜMÜLATİF PERFORMANS ÖZETİ (2015 - 2026)")
    print(sep)
    print(f"  • Toplam İşlem Sayısı                : {rep_total.total_trades}")
    print(f"  • Kazanma Oranı (Win Rate)           : %{rep_total.win_rate_pct:.1f}")
    print(f"  • Kâr Faktörü (Profit Factor)        : {rep_total.profit_factor:.2f}")
    print(f"  • Ortalama Kâr/Zarar (Payoff Ratio)  : {rep_total.payoff_ratio:.2f}")
    print(f"  • Toplam Net Getiri (R-Cinsi)        : +{rep_total.total_r:.2f} R")
    print(f"  • Sabit %0.5 Risk Net Kârı ($)       : +${rep_total.total_pnl_dollars:,.2f} (+%{rep_total.return_on_capital_pct:.1f})")
    print(f"  • Maksimum Çekilme (Max Drawdown R)  : -{max_dd_r:.2f} R")
    print(f"  • Sabit Risk Max Drawdown (%)        : -%{rep_total.max_drawdown_pct:.2f}")
    print(f"  • 🚀 %1.5 BİLEŞİK FİNAL SERMAYE ($)  : ${compounding_capital:,.2f} (+%{(compounding_capital - 10000.0)/100:.1f} / {(compounding_capital/10000.0):.1f}x KAT)")
    print(f"  • Toplam Kırmızı Kart (Atılma Sayısı): {len(evictions)} kez trendi bozan atıldı")

    print("\n" + sep)
    print("🚨 [BÖLÜM 3] 12 YILLIK KIRMIZI KART / ÇIKARILMA GEREKÇELERİ DAĞILIMI")
    print(sep)
    for rk, rc in reasons_count.items():
        print(f"  • {rk:<40}: {rc:>3} kez kırmızı kart")
    print(sep + "\n")

if __name__ == "__main__":
    run_12y_multiyear_backtest()
