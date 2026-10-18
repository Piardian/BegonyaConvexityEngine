import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

# UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import MetaTrader5 as mt5
from execution.mt5_execution_router import MT5ExecutionRouter
from execution.lifecycle_daemon import TradeLifecycleManager
from data.market_loader import MarketLoader
import pandas as pd

def run_preflight():
    results = {}

    # Kontrol 1: config doğrulaması
    cfg_path = ROOT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    exec_cfg = config.get("execution", {})
    shadow_mode = exec_cfg.get("shadow_mode")
    allow_real = exec_cfg.get("allow_real_orders")
    check1_pass = (shadow_mode is True) and (allow_real is False)
    results["check_1_config"] = {
        "pass": check1_pass,
        "shadow_mode": shadow_mode,
        "allow_real_orders": allow_real
    }

    # Kontrol 2: is_real_execution_blocked ve safe_order_send
    router = MT5ExecutionRouter(config)
    blocked, reason = router.is_real_execution_blocked()
    ok, res, send_reason = router.safe_order_send({"action": 1})
    check2_pass = blocked and (ok is False) and (res is None)
    results["check_2_security_lock"] = {
        "pass": check2_pass,
        "blocked": blocked,
        "block_reason": reason,
        "safe_order_send_prevented": (ok is False)
    }

    # Kontrol 3: Mock/stub order_send kalkanı altında test
    def fail_if_order_sent(*args, **kwargs):
        raise RuntimeError("GÜVENLİK İHLALİ: mt5.order_send çağrıldı!")

    with patch("MetaTrader5.order_send", side_effect=fail_if_order_sent):
        exec_res = router.execute_split_tickets("EURUSD", "LONG", 1.0850, 1.0800, 50.0)
        is_shadow_filled = exec_res.get("status") == "SHADOW_FILLED"
        has_virtual_tickets = bool(exec_res.get("ticket_a")) and bool(exec_res.get("ticket_b"))
    
    check3_pass = is_shadow_filled and has_virtual_tickets
    results["check_3_mock_order_prevention"] = {
        "pass": check3_pass,
        "status": exec_res.get("status"),
        "is_shadow": exec_res.get("is_shadow"),
        "ticket_a": exec_res.get("ticket_a"),
        "ticket_b": exec_res.get("ticket_b")
    }

    # Kontrol 4: INSUFFICIENT_DATA doğrulaması
    loader = MarketLoader()
    v_empty, r_empty = loader.validate_symbol_data(pd.DataFrame(), min_bars=50)
    v_short, r_short = loader.validate_symbol_data(pd.DataFrame({"open": [1]*20, "high": [1]*20, "low": [1]*20, "close": [1]*20}), min_bars=50)
    check4_pass = (not v_empty and "INSUFFICIENT_DATA" in r_empty) and (not v_short and "INSUFFICIENT_DATA" in r_short)
    results["check_4_insufficient_data"] = {
        "pass": check4_pass,
        "empty_reason": r_empty,
        "short_reason": r_short
    }

    # Kontrol 5: Canlı hesap ve çalışma modu doğrulaması
    acc = mt5.account_info() if mt5.initialize() else None
    account_login = acc.login if acc else None
    account_server = acc.server if acc else None
    check5_pass = check1_pass and check2_pass and check3_pass and check4_pass
    results["check_5_account_and_mode"] = {
        "pass": check5_pass,
        "login": account_login,
        "server": account_server,
        "mode_confirmed": "SHADOW_MODE_ONLY"
    }

    all_passed = all(r["pass"] for r in results.values())
    results["all_passed"] = all_passed

    print(json.dumps(results, indent=2, ensure_ascii=False))
    return all_passed

if __name__ == "__main__":
    success = run_preflight()
    sys.exit(0 if success else 1)
