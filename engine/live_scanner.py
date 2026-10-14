import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional
import pandas as pd
from datetime import datetime
from analytics.benchmark_collector import BenchmarkCollector

logger = logging.getLogger("LiveScanner")

class LiveConvexityScanner:
    """
    Canlı / Güncel piyasa verilerini tarayarak Asimetrik Risk & D1 Trend Takipçiliği
    modeline göre bugün işlem sinyali veren varlıkları ve seviyelerini çıkaran bağımsız motor.
    BenchmarkCollector ile günlük sinyal ve radar verilerini JSON/JSONL olarak arşivler.
    """
    def __init__(self, config: dict, market_loader, trend_detector, benchmark_collector: Optional[BenchmarkCollector] = None):
        self.config = config
        self.loader = market_loader
        self.detector = trend_detector
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

            sig = self.detector.check_breakout(df, last_idx, symbol)

            # Mevcut trend durumunu değerlendir
            close = latest_bar["close"]
            ema200 = latest_bar.get("ema200", close)
            atr = latest_bar["atr14"]
            high_20 = latest_bar["donchian_high_20"]
            low_20 = latest_bar["donchian_low_20"]

            regime = "BOĞA REJİMİ (EMA200 Üstü)" if close > ema200 else "AYI REJİMİ (EMA200 Altı)"
            
            if sig:
                # Bugün taze bir kırılım oluştu! (Saf Matematiksel Onay)
                entry = sig.entry_price
                sl = sig.initial_sl
                risk = sig.risk_distance
                partial_tp = entry + (self.partial_tp_r * risk) if sig.direction == "LONG" else entry - (self.partial_tp_r * risk)
                
                status_text = "🚨 TAZE KIRILIM - MATEMATİKSEL ONAYLI"

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
                    "macro_regime": "Saf Matematiksel Model (Bağımsız)",
                    "macro_bias": "LONG_ONLY (EMA200 + ATR Rotasyonu)",
                    "macro_allowed": True,
                    "macro_risk_multiplier": 1.0,
                    "macro_status_message": "Tam Risk (1.00x) - Bağımsız Matematiksel Onay",
                    "status": status_text
                })
            else:
                # Kırılıma yakınlık kontrolü (Radardakiler)
                dist_to_high_pct = ((high_20 - close) / close) * 100

                radar_status = None
                radar_dir = "LONG"
                if close > ema200 and 0 < dist_to_high_pct <= 1.5:
                    radar_status = f"👀 20 Günlük Zirveye %{dist_to_high_pct:.1f} Kaldı (Long Radarı)"
                    radar_dir = "LONG"

                if radar_status:
                    actionable_signals.append({
                        "symbol": symbol,
                        "type": "WATCHLIST_RADAR",
                        "direction": "RADAR",
                        "radar_direction": radar_dir,
                        "date": str(latest_bar.name.date()),
                        "current_price": close,
                        "entry_price": high_20,
                        "stop_loss": 0.0,
                        "risk_distance": 0.0,
                        "partial_tp_2r": 0.0,
                        "regime": regime,
                        "atr14": atr,
                        "macro_regime": "Saf Matematiksel Model (Bağımsız)",
                        "macro_bias": "LONG_ONLY (EMA200 + ATR Rotasyonu)",
                        "macro_allowed": True,
                        "macro_status_message": "Kırılım Bekleniyor",
                        "status": radar_status
                    })

        # Günlük Benchmark ve Tarihsel Veri Kaydı (signals/history & logs/benchmark_signals.jsonl)
        try:
            self.benchmark_collector.log_scan_snapshot(actionable_signals)
        except Exception as e:
            logger.error(f"Benchmark loglama hatası: {e}")

        return actionable_signals
