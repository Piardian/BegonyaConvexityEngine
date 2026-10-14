"""Read-only consistency check for Convexity Engine risk settings."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "convexity_config.json"


def main() -> int:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    backtest = config.get("backtest", {})
    execution = config.get("execution", {})
    mechanics = config.get("mechanics", {})
    backtest_risk = backtest.get("risk_per_trade_pct")
    execution_risk = execution.get("risk_per_trade_pct")
    print("# Convexity Engine Risk Configuration Audit")
    print(f"- Config: {CONFIG_PATH}")
    print(f"- Backtest risk per trade: {backtest_risk}%")
    print(f"- Execution risk per trade: {execution_risk}%")
    print(f"- Shadow mode: {execution.get('shadow_mode', 'NOT_SET')}")
    print(f"- Real orders allowed: {execution.get('allow_real_orders', 'NOT_SET')}")
    print(f"- Break-even trigger: {mechanics.get('be_trigger_r', 'NOT_SET')}R")
    print(f"- Partial take-profit: {mechanics.get('partial_tp_r', 'NOT_SET')}R")
    print(f"- Trailing ATR multiplier: {mechanics.get('trailing_atr_multiplier', 'NOT_SET')}x")
    print(f"- Commission/spread assumption: {backtest.get('commission_spread_r_per_trade', 'NOT_SET')}R per trade")
    problems = []
    if not isinstance(backtest_risk, (int, float)) or backtest_risk <= 0:
        problems.append("backtest.risk_per_trade_pct must be a positive number")
    if not isinstance(execution_risk, (int, float)) or execution_risk <= 0:
        problems.append("execution.risk_per_trade_pct must be a positive number")
    if isinstance(backtest_risk, (int, float)) and isinstance(execution_risk, (int, float)) and backtest_risk != execution_risk:
        problems.append("backtest and execution risk percentages differ; confirm this is intentional")
    if not isinstance(execution.get("shadow_mode"), bool):
        problems.append("execution.shadow_mode must be explicitly true/false")
    if not isinstance(execution.get("allow_real_orders"), bool):
        problems.append("execution.allow_real_orders must be explicitly true/false")
    if problems:
        print("\n## Findings")
        for problem in problems:
            print(f"- WARNING: {problem}")
        return 1
    print("\n- Required fields are present. This config audit does not prove runtime enforcement or broker behavior.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
