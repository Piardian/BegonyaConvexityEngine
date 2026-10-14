# Convexity Engine Risk and Validation Protocol

## Read-only config audit

From the repository root:

```bash
python scripts/audit_risk_config.py
python -m py_compile run_convexity_backtest.py scripts/audit_risk_config.py
python run_convexity_backtest.py
```

The audit compares the risk percentage configured for the backtest and execution layers, and reports shadow/live-order flags plus the mechanics values shown by the backtest banner. It does not start the daemon or send orders.

## Risk interpretation

- The backtest simulator reads `backtest.risk_per_trade_pct`; execution configuration is a separate setting and must be intentionally aligned or explicitly documented as different.
- The backtest report's cost deduction is a declared `commission_spread_r_per_trade` assumption. It is not a broker-verified cost estimate.
- A config file that says `allow_real_orders: false` is not, by itself, proof that the running process cannot send orders. Verify the active code path and runtime mode before deployment.
- Never use backtest results to claim live profitability without checking data coverage, look-ahead, fills, spread, slippage, and portfolio-level exposure.
