import os
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime

# Windows UTF-8
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
from execution.mt5_execution_router import MT5ExecutionRouter
from execution.telemetry_logger import TelemetryLogger
from execution.macro_gate_adapter import MacroGateAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SignalEngine")

def load_config() -> dict:
    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)

def run_pipeline(dry_run: bool = True):
    config = load_config()
    telemetry = TelemetryLogger()
    router = MT5ExecutionRouter(config, telemetry=telemetry)
    loader = MarketLoader()
    detector = TrendDetector(config)
    macro_gate = MacroGateAdapter()

    # 12 Temizlenmiş Trend Varlığı Sepeti
    chop_pairs = ["USDCAD", "GBPJPY", "EURJPY", "AUDUSD"]
    symbols = [s for s in config.get("backtest", {}).get("symbols", []) if s not in chop_pairs]

    print("\n" + "═" * 85)
    print(" 🚀 BEGONYA CONVEXITY D1 CANLI SİNYAL & İNFAZ PİPELİNE'I 🚀 ")
    mode_str = "🟡 SİMÜLASYON / DRY-RUN (Emir İletilmez)" if dry_run else "🔴 CANLI İNFAZ / LIVE MT5 (Gerçek Emir İletilir)"
    print(f"    Mod: {mode_str}")
    print(f"    Zaman: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Varlık Sayısı: {len(symbols)}")
    print("═" * 85)

    signals_found = 0

    for symbol in symbols:
        df = loader.load_symbol_data(symbol, period="1y")
        if df.empty or len(df) < 50:
            continue

        latest_idx = len(df) - 1
        sig = detector.check_breakout(df, latest_idx, symbol)

        if sig:
            signals_found += 1
            print(f"\n⚡ [TEKNİK KIRILIM ONAYLANDI] {sig.direction} {symbol} @ {sig.entry_price:.4f} | SL: {sig.initial_sl:.4f} (1.5x ATR)")

            # 🌺 BEGONYA MAKRO KAPISI DENETİMİ
            gate_eval = macro_gate.evaluate_candidate(symbol, sig.direction)
            print(f"   🏛️ [MAKRO KAPI]: {gate_eval.status_message}")
            print(f"   🏛️ [REJİM]: {gate_eval.primary_regime}")

            if not gate_eval.allowed:
                print(f"   🛑 [VETO EDİLDİ]: Begonya Makro Kapısı bu yönde işleme izin vermedi. İnfaz engellendi!")
                continue

            base_risk_usd = 10.0 # Test mikro riski
            actual_risk_usd = round(base_risk_usd * gate_eval.risk_multiplier, 2)
            print(f"   ✅ [ONAYLANDI]: Dinamik Makro Risk: ${actual_risk_usd} ({gate_eval.risk_multiplier}x)")
            
            # Router üzerinden sembolü çöz ve lotu hesapla
            broker_sym = router.mapper.resolve(symbol)
            if broker_sym:
                info = router.calculate_dynamic_lot(broker_sym, sig.entry_price, sig.initial_sl, actual_risk_usd)
                print(f"   ➔ Broker Sembolü: {broker_sym} | Hesaplanmış Dinamik Lot: {info} Lot")

            if not dry_run:
                print(f"   ➔ MT5 Execution Router'a emir gönderiliyor...")
                res = router.execute_signal(symbol, sig.direction, sig.initial_sl)
                print(f"   ➔ İnfaz Sonucu: {res.get('status')} | Ticket: {res.get('order_ticket', 'N/A')}")
            else:
                print(f"   ➔ [Dry-Run]: Canlı emir pas geçildi. Telemetry hazır.")

    if signals_found == 0:
        print("\n🔍 Bugün için 12 enstrümanda yeni D1 Donchian kırılımı bulunamadı. Mevcut trendler devam ediyor.")

    print("\n" + "═" * 85)
    router.shutdown()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Begonya Signal Engine & MT5 Execution Router")
    parser.add_argument("--live", action="store_true", help="Gerçek MT5 emir infazı modunu etkinleştirir")
    args = parser.parse_args()

    run_pipeline(dry_run=not args.live)
