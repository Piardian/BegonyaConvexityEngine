import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger("BenchmarkCollector")

class BenchmarkCollector:
    """
    Begonya Convexity Engine için merkezi Benchmark & Doğrulama Veri Toplayıcısı.
    1. Günlük Sinyal & Radar Snapshot'larını arşivler (signals/history/ ve logs/benchmark_signals.jsonl).
    2. Backtest metriklerini ve strateji parametrelerini kıyaslama havuzunda saklar (analytics/benchmarks/backtest_history.json).
    3. Canlı kapanış sonuçlarını ve sapmaları (slippage, R realization) kaydeder.
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or Path(__file__).resolve().parent.parent
        self.history_dir = self.base_dir / "signals" / "history"
        self.benchmarks_dir = self.base_dir / "analytics" / "benchmarks"
        self.logs_dir = self.base_dir / "logs"

        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.benchmarks_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self.signals_jsonl_path = self.logs_dir / "benchmark_signals.jsonl"
        self.backtest_history_path = self.benchmarks_dir / "backtest_history.json"
        self.trade_outcomes_path = self.logs_dir / "benchmark_trade_outcomes.jsonl"

    def log_scan_snapshot(self, signals: List[Dict[str, Any]], metadata: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        """
        Günlük tarama sonucunu hem gün bazlı JSON snapshot dosyasına
        hem de kümülatif zaman serisi JSONL kütüğüne işler.
        """
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        iso_now = now.isoformat()

        snapshot_payload = {
            "timestamp": iso_now,
            "scan_date": date_str,
            "total_signals": len(signals),
            "fresh_breakouts_count": len([s for s in signals if s.get("type") == "FRESH_BREAKOUT"]),
            "watchlist_radar_count": len([s for s in signals if s.get("type") == "WATCHLIST_RADAR"]),
            "macro_vetoed_count": len([s for s in signals if not s.get("macro_allowed", True)]),
            "metadata": metadata or {},
            "signals": signals
        }

        # 1. Günlük snapshot JSON dosyası (signals/history/signals_YYYY-MM-DD.json)
        daily_file = self.history_dir / f"signals_{date_str}.json"
        try:
            with open(daily_file, "w", encoding="utf-8") as f:
                json.dump(snapshot_payload, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Günlük sinyal snapshot dosyası yazılamadı ({daily_file}): {e}")

        # 2. Kümülatif JSONL kütüğü (logs/benchmark_signals.jsonl)
        try:
            with open(self.signals_jsonl_path, "a", encoding="utf-8") as f:
                for sig in signals:
                    entry = {
                        "timestamp": iso_now,
                        "scan_date": date_str,
                        **sig
                    }
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Benchmark sinyal JSONL kütüğü yazılamadı: {e}")

        return {
            "daily_snapshot": str(daily_file),
            "cumulative_jsonl": str(self.signals_jsonl_path)
        }

    def log_backtest_benchmark(
        self,
        portfolio_report,
        config: Dict[str, Any],
        symbol_reports: Optional[List[Any]] = None,
        notes: str = ""
    ) -> str:
        """
        Backtest metriklerini ve kullanılan parametreleri benchmark havuzuna kaydeder.
        Böylece strateji güncellemelerinin geçmişe göre performansı test edilebilir.
        """
        now = datetime.now()
        iso_now = now.isoformat()

        benchmark_entry = {
            "benchmark_id": f"BT_{now.strftime('%Y%m%d_%H%M%S')}",
            "timestamp": iso_now,
            "notes": notes,
            "parameters": {
                "strategy_name": config.get("strategy_name", "Begonya Convexity Engine"),
                "timeframe": config.get("timeframe", "1d"),
                "initial_capital": config.get("backtest", {}).get("initial_capital", 10000.0),
                "risk_per_trade_pct": config.get("backtest", {}).get("risk_per_trade_pct", 0.5),
                "donchian_breakout_period": config.get("mechanics", {}).get("donchian_breakout_period", 20),
                "atr_period": config.get("mechanics", {}).get("atr_period", 14),
                "sl_atr_multiplier": config.get("mechanics", {}).get("sl_atr_multiplier", 1.5),
                "partial_tp_r": config.get("mechanics", {}).get("partial_tp_r", 2.0),
                "partial_close_ratio": config.get("mechanics", {}).get("partial_close_ratio", 0.50),
                "trailing_atr_multiplier": config.get("mechanics", {}).get("trailing_atr_multiplier", 2.0),
                "require_ema200_alignment": config.get("mechanics", {}).get("require_ema200_alignment", True),
                "symbols": config.get("backtest", {}).get("symbols", [])
            },
            "portfolio_metrics": {
                "total_trades": portfolio_report.total_trades,
                "wins": portfolio_report.wins,
                "losses": portfolio_report.losses,
                "win_rate_pct": portfolio_report.win_rate_pct,
                "total_r": portfolio_report.total_r,
                "total_pnl_dollars": portfolio_report.total_pnl_dollars,
                "return_on_capital_pct": portfolio_report.return_on_capital_pct,
                "profit_factor": portfolio_report.profit_factor,
                "avg_win_r": portfolio_report.avg_win_r,
                "avg_loss_r": portfolio_report.avg_loss_r,
                "payoff_ratio": portfolio_report.payoff_ratio,
                "expectancy_r": portfolio_report.expectancy_r,
                "max_drawdown_r": portfolio_report.max_drawdown_r,
                "max_drawdown_pct": portfolio_report.max_drawdown_pct,
                "max_consecutive_losses": portfolio_report.max_consecutive_losses,
                "avg_holding_days": portfolio_report.avg_holding_days
            },
            "symbol_breakdown": {}
        }

        if symbol_reports:
            for rep in symbol_reports:
                benchmark_entry["symbol_breakdown"][rep.name] = {
                    "total_trades": rep.total_trades,
                    "win_rate_pct": rep.win_rate_pct,
                    "avg_win_r": rep.avg_win_r,
                    "avg_loss_r": rep.avg_loss_r,
                    "payoff_ratio": rep.payoff_ratio,
                    "total_r": rep.total_r,
                    "profit_factor": rep.profit_factor,
                    "max_drawdown_r": rep.max_drawdown_r
                }

        # Mevcut geçmişi yükle
        history = []
        if self.backtest_history_path.exists():
            try:
                with open(self.backtest_history_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
                    if not isinstance(history, list):
                        history = []
            except Exception:
                history = []

        history.append(benchmark_entry)

        try:
            with open(self.backtest_history_path, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Backtest benchmark dosyası kaydedilemedi: {e}")

        return str(self.backtest_history_path)

    def log_trade_outcome(self, outcome_data: Dict[str, Any]):
        """
        Canlı işlem kapanış sonuçlarını, gerçekleşen R ile hedeflenen R arasındaki
        sapmaları ve slippage analizini JSONL kütüğüne işler.
        """
        outcome_data["logged_at"] = datetime.now().isoformat()
        try:
            with open(self.trade_outcomes_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(outcome_data, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Trade outcome benchmark kütüğü yazılamadı: {e}")
