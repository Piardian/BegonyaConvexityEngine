import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional
import pandas as pd
from datetime import datetime
from execution.macro_gate_adapter import MacroGateAdapter
from analytics.benchmark_collector import BenchmarkCollector

logger = logging.getLogger("LiveScanner")

class LiveConvexityScanner:
    """
    Canlı / Güncel piyasa verilerini tarayarak Asimetrik Risk & D1 Trend Takipçiliği
    modeline göre bugün işlem sinyali veren varlıkları ve seviyelerini çıkaran motor.
    Begonya Makro Kapısı (MacroGateAdapter) ile çift katmanlı filtreleme yapar.
    BenchmarkCollector ile günlük sinyal ve radar verilerini JSON/JSONL olarak arşivler.
    """
    def __init__(self, config: dict, market_loader, trend_detector, benchmark_collector: Optional[BenchmarkCollector] = None):
        self.config = config
        self.loader = market_loader
        self.detector = trend_detector
        self.macro_gate = MacroGateAdapter()
        self.benchmark_collector = benchmark_collector or BenchmarkCollector()
        self.mech = config.get("mechanics", {})
        self.symbols = config.get("backtest", {}).get("symbols", [])
        self.sl_atr_mult = self.mech.get("sl_atr_multiplier", 1.5)
        self.partial_tp_r = self.mech.get("partial_tp_r", 2.0)
        self.trailing_atr_mult = self.mech.get("trailing_atr_multiplier", 2.0)

    def scan_all_symbols(self) -> List[dict]:
        """Tüm 16 küresel varlığı güncel günlük kapanışlarına göre tarar."""
        actionable_signals = []

        for symbol in self.symbols:
            df = self.loader.load_symbol_data(symbol, period="1y")
            if df.empty or len(df) < 50:
                continue

            last_idx = len(df) - 1
            latest_bar = df.iloc[-1]
            prev_bar = df.iloc[-2]

            sig = self.detector.check_breakout(df, last_idx, symbol)

            # Mevcut trend durumunu değerlendir
            close = latest_bar["close"]
            ema200 = latest_bar.get("ema200", close)
            ema20 = latest_bar.get("ema20", close)
            atr = latest_bar["atr14"]
            high_20 = latest_bar["donchian_high_20"]
            low_20 = latest_bar["donchian_low_20"]

            regime = "BOĞA REJİMİ (EMA200 Üstü)" if close > ema200 else "AYI REJİMİ (EMA200 Altı)"
            
            if sig:
                # Bugün taze bir kırılım oluştu!
                entry = sig.entry_price
                sl = sig.initial_sl
                risk = sig.risk_distance
                partial_tp = entry + (self.partial_tp_r * risk) if sig.direction == "LONG" else entry - (self.partial_tp_r * risk)
                
                # Makro Kapı Denetimi
                gate_eval = self.macro_gate.evaluate_candidate(symbol, sig.direction)

                status_text = "🚨 TAZE KIRILIM - MAKRO ONAYLI" if gate_eval.allowed else f"🛑 KIRILIM VETO ({gate_eval.status_message})"

                actionable_signals.append({
                    "symbol": symbol,
                    "type": "FRESH_BREAKOUT",
                    "direction": sig.direction,
                    "date": str(latest_bar.name.date()),
                    "current_price": close,
                    "entry_price": entry,
                    "stop_loss": sl,
                    "risk_distance": risk,
                    "partial_tp_2r": partial_tp,
                    "regime": regime,
                    "atr14": atr,
                    "macro_regime": gate_eval.primary_regime,
                    "macro_bias": gate_eval.macro_bias,
                    "macro_allowed": gate_eval.allowed,
                    "macro_risk_multiplier": gate_eval.risk_multiplier,
                    "macro_status_message": gate_eval.status_message,
                    "status": status_text
                })
            else:
                # Kırılıma yakınlık kontrolü (Radardakiler)
                dist_to_high_pct = ((high_20 - close) / close) * 100
                dist_to_low_pct = ((close - low_20) / close) * 100

                radar_status = None
                radar_dir = "LONG"
                if close > ema200 and 0 < dist_to_high_pct <= 1.5:
                    radar_status = f"👀 20 Günlük Zirveye %{dist_to_high_pct:.1f} Kaldı (Long Radarı)"
                    radar_dir = "LONG"
                elif close < ema200 and 0 < dist_to_low_pct <= 1.5:
                    radar_status = f"👀 20 Günlük Dibe %{dist_to_low_pct:.1f} Kaldı (Short Radarı)"
                    radar_dir = "SHORT"

                if radar_status:
                    gate_eval = self.macro_gate.evaluate_candidate(symbol, radar_dir)
                    actionable_signals.append({
                        "symbol": symbol,
                        "type": "WATCHLIST_RADAR",
                        "direction": "RADAR",
                        "radar_direction": radar_dir,
                        "date": str(latest_bar.name.date()),
                        "current_price": close,
                        "entry_price": high_20 if "Long" in radar_status else low_20,
                        "stop_loss": 0.0,
                        "risk_distance": 0.0,
                        "partial_tp_2r": 0.0,
                        "regime": regime,
                        "atr14": atr,
                        "macro_regime": gate_eval.primary_regime,
                        "macro_bias": gate_eval.macro_bias,
                        "macro_allowed": gate_eval.allowed,
                        "macro_status_message": gate_eval.status_message,
                        "status": radar_status
                    })

        # Günlük Benchmark ve Tarihsel Veri Kaydı (signals/history & logs/benchmark_signals.jsonl)
        try:
            self.benchmark_collector.log_scan_snapshot(actionable_signals)
        except Exception as e:
            logger.error(f"Benchmark loglama hatası: {e}")

        return actionable_signals
