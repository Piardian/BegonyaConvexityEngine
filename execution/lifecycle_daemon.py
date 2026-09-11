import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta
import MetaTrader5 as mt5

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
from engine.rotational_engine import RotationalSelectionEngine
from execution.mt5_execution_router import MT5ExecutionRouter
from execution.telemetry_logger import TelemetryLogger
from execution.macro_gate_adapter import MacroGateAdapter
from analytics.benchmark_collector import BenchmarkCollector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("LifecycleManager")

def load_config() -> dict:
    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)

class TradeLifecycleManager:
    """
    Begonya D1 Convexity Yaşam Döngüsü Yöneticisi:
    1. Açık Pozisyon Denetimi: Magic 260902 (Runner) biletlerinin stopunu 2.5x ATR ile günceller.
    2. Dinamik Rotasyon: 16 varlıkta 200 EMA, volatilite ve karantina filtreleriyle aktif evreni belirler.
    3. Yeni Sinyal Taraması: Aktif listedeki varlıklarda Donchian 20 + 200 EMA breakout tespit eder.
    4. Makro Kapı Filtresi: Begonya'nın canlı makro rejim ve kapı verileriyle yön ve kriz veto denetimi yapar.
    5. İki Bilet İnfazı: Bilet A (+2.0R TP) ve Bilet B (Trailing Runner).
    6. Kapanış Takibi: TP veya SL vuran biletleri algılar ve Telegram'a 'Matematiksel Bot' başlığıyla bildirir.
    """

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.config = load_config()
        self.telemetry = TelemetryLogger()
        self.router = MT5ExecutionRouter(self.config, telemetry=self.telemetry)
        self.loader = MarketLoader()
        self.detector = TrendDetector(self.config)
        self.rot_engine = RotationalSelectionEngine(self.config)
        self.macro_gate = MacroGateAdapter()
        self.benchmark_collector = BenchmarkCollector()

        self.state_dir = PROJECT_DIR / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.tickets_file = self.state_dir / "active_tickets.json"

        # 16 Çoklu Küresel Varlık Evreni
        self.symbols = self.config.get("backtest", {}).get("symbols", [])

    def _load_tracked_tickets(self) -> dict:
        if self.tickets_file.exists():
            try:
                with open(self.tickets_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_tracked_tickets(self, tickets: dict):
        try:
            with open(self.tickets_file, "w", encoding="utf-8") as f:
                json.dump(tickets, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Aktif bilet dosyası yazılamadı: {e}")

    def check_and_notify_closures(self):
        """Kapanan biletleri tespit eder, net PnL hesaplar ve Telegram'a bildirir."""
        if not mt5.initialize():
            return

        tracked = self._load_tracked_tickets()
        if not tracked:
            return

        open_positions = mt5.positions_get()
        open_tickets = {p.ticket for p in open_positions} if open_positions else set()

        closed_tickets = []

        for ticket_str, data in tracked.items():
            ticket_id = int(ticket_str)
            if ticket_id not in open_tickets:
                # Pozisyon kapanmış! MT5 tarihçesinden deal'i sorgula
                deals = mt5.history_deals_get(position=ticket_id)
                pnl_dollars = 0.0
                exit_price = 0.0
                close_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if deals:
                    # Kapanış deal'i (genellikle sonuncu)
                    closing_deal = deals[-1]
                    pnl_dollars = round(sum(d.profit + d.commission + d.swap for d in deals), 2)
                    exit_price = closing_deal.price
                    close_time = datetime.fromtimestamp(closing_deal.time).strftime("%Y-%m-%d %H:%M:%S")

                risk_usd = data.get("risk_usd", 10.0)
                pnl_r = round(pnl_dollars / risk_usd, 2) if risk_usd > 0 else 0.0

                reason = "Hedef Vuruldu (TP)" if pnl_dollars > 0 else "Zarar Kes (SL)"

                event = {
                    "status": "CLOSED",
                    "action": "CLOSE",
                    "symbol": data.get("symbol", ""),
                    "ticket": ticket_id,
                    "reason": reason,
                    "pnl_r": pnl_r,
                    "pnl_usd": pnl_dollars,
                    "exit_price": exit_price,
                    "close_time": close_time
                }
                self.telemetry.log_execution(event)
                try:
                    self.benchmark_collector.log_trade_outcome(event)
                except Exception as e:
                    logger.error(f"Benchmark kapanış kaydı hatası: {e}")
                closed_tickets.append(ticket_str)

        # Kapanan biletleri takipten çıkar
        if closed_tickets:
            for t in closed_tickets:
                tracked.pop(t, None)
            self._save_tracked_tickets(tracked)

    def execute_daily_cycle(self):
        print("\n" + "═" * 90)
        print(" ⏳ BEGONYA CONVEXITY D1 YAŞAM DÖNGÜSÜ DÖNGÜSÜ BAŞLADI ⏳ ")
        mode_str = "🟡 SİMÜLASYON / DRY-RUN" if self.dry_run else "🔴 CANLI İNFAZ / MT5 LIVE"
        print(f"    Zaman: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Mod: {mode_str}")
        print("═" * 90)

        # 0. Kapanan bilet denetimi
        self.check_and_notify_closures()

        # Güncel D1 verilerini çek
        d1_market_cache = {}
        for sym in self.symbols:
            df = self.loader.load_symbol_data(sym, period="1y")
            if not df.empty and len(df) >= 20:
                broker_sym = self.router.mapper.resolve(sym)
                last_bar = df.iloc[-1]
                recent_high = df["high"].iloc[-5:].max()
                recent_low = df["low"].iloc[-5:].min()
                data_dict = {
                    "df": df,
                    "atr14": float(last_bar["atr14"]),
                    "close": float(last_bar["close"]),
                    "recent_high": float(recent_high),
                    "recent_low": float(recent_low),
                }
                d1_market_cache[sym] = data_dict
                if broker_sym:
                    d1_market_cache[broker_sym] = data_dict

        # =========================================================================
        # 1. AŞAMA: AÇIK POZİSYON DENETİMİ (TRAILING STOP GÜNCELLEMESİ)
        # =========================================================================
        print("\n[ADIM 1] Açık Runner Biletleri Denetleniyor (2.5x ATR Trailing Stop)...")
        if not self.dry_run:
            trail_updates = self.router.update_runner_trailing_stops(d1_market_cache)
            if trail_updates:
                print(f"   ✅ {len(trail_updates)} adet pozisyonun stop seviyesi kârı koruyacak şekilde yukarı taşındı.")
            else:
                print("   ℹ️ Stop seviyesini yukarı taşımayı gerektirecek yeni bir zirve/dip oluşmadı.")
        else:
            print("   ℹ️ [Dry-Run] Açık pozisyon stop kontrolü simüle edildi.")

        # =========================================================================
        # 2. AŞAMA: DİNAMİK ROTASYON VE AKTİF LİSTE BELİRLEME
        # =========================================================================
        print("\n[ADIM 2] 16 Varlıkta Dinamik Rotasyon & Kırmızı Kart Taraması...")
        now = datetime.now()
        active_symbols, audit_events = self.rot_engine.evaluate_and_rebalance(
            current_date=now,
            symbol_data_map={s: d1_market_cache[s]["df"] for s in self.symbols if s in d1_market_cache},
            open_positions=set(self._load_tracked_tickets().keys())
        )
        print(f"   🟢 Aktif Listede Olanlar ({len(active_symbols)} Varlık): {', '.join(sorted(list(active_symbols)))}")
        evicted = [a for a in audit_events if a.action == "EVICTED"]
        if evicted:
            print(f"   🔴 Kırmızı Kart Görenler ({len(evicted)} Varlık):")
            for ev in evicted:
                print(f"      • {ev.symbol}: {ev.reason}")

        # =========================================================================
        # 3. AŞAMA: YENİ SİNYAL TARAMASI VE İKİ BİLET İNFAZI
        # =========================================================================
        print("\n[ADIM 3] Aktif Varlıklarda D1 Donchian + 200 EMA Kırılımları Taranıyor...")
        new_signals = 0
        tracked_tickets = self._load_tracked_tickets()

        # MT5 Hesap Bakiyesini Al ve %2.0 Risk Hesapla
        acc = mt5.account_info() if mt5.initialize() else None
        balance = acc.balance if acc else self.config.get("backtest", {}).get("initial_capital", 10000.0)
        risk_pct = self.config.get("execution", {}).get("risk_per_trade_pct", 2.0) / 100.0
        base_risk_usd = balance * risk_pct

        for sym in self.symbols:
            if sym not in d1_market_cache:
                continue
            
            # Sadece aktif listede olan varlıklarda işlem aç!
            if sym not in active_symbols:
                continue

            df = d1_market_cache[sym]["df"]
            latest_idx = len(df) - 1
            sig = self.detector.check_breakout(df, latest_idx, sym)

            if sig:
                broker_sym = self.router.mapper.resolve(sym)
                print(f"\n⚡ [TEKNİK KIRILIM ONAYLANDI] {sig.direction} {sym} ({broker_sym}) @ {sig.entry_price:.4f} | SL: {sig.initial_sl:.4f}")
                new_signals += 1

                # 🌺 BEGONYA MAKRO KAPISI DENETİMİ
                gate_eval = self.macro_gate.evaluate_candidate(sym, sig.direction)
                print(f"   🏛️ [MAKRO KAPI]: {gate_eval.status_message}")
                print(f"   🏛️ [REJİM]: {gate_eval.primary_regime}")

                if not gate_eval.allowed:
                    print(f"   🛑 [MAKRO VETO]: Begonya Makro Kapısı ({gate_eval.macro_bias}) bu işlemi kilitledi. İnfaz iptal edildi!")
                    continue

                # Mükerrer İşlem Koruması (State Lock)
                if broker_sym:
                    existing = self.router.get_open_positions_for_symbol(broker_sym)
                    if existing:
                        print(f"   ⚠️ [STATE LOCK]: Bu sembolde zaten açık bilet (#{existing[0].ticket}) var. Çift yönlü işlem kilitlendi!")
                        continue

                target_risk_usd = round(base_risk_usd * gate_eval.risk_multiplier, 2)
                print(f"   ✅ [MAKRO ONAYLI İNFAZ]: Bakiye: ${balance:,.2f} | Risk: %{risk_pct*100:.1f} (${target_risk_usd:,.2f})")

                if not self.dry_run:
                    print(f"   ➔ İki Bilet (Split Ticket) İletiliyor: Bilet A (+2.0R TP) + Bilet B (Runner)...")
                    res = self.router.execute_split_tickets(sym, sig.direction, sig.entry_price, sig.initial_sl, target_risk_usd)
                    print(f"   ➔ İnfaz Durumu: {res.get('status')} | Bilet A: #{res.get('ticket_a')} | Bilet B: #{res.get('ticket_b')}")

                    # Başarılı biletleri takibe al
                    if res.get("ticket_a"):
                        tracked_tickets[str(res["ticket_a"])] = {
                            "symbol": broker_sym, "type": "TICKET_A_TP", "risk_usd": target_risk_usd / 2.0, "entry": sig.entry_price
                        }
                    if res.get("ticket_b"):
                        tracked_tickets[str(res["ticket_b"])] = {
                            "symbol": broker_sym, "type": "TICKET_B_RUNNER", "risk_usd": target_risk_usd / 2.0, "entry": sig.entry_price
                        }
                    self._save_tracked_tickets(tracked_tickets)
                else:
                    lot = self.router.calculate_dynamic_lot(broker_sym or sym, sig.entry_price, sig.initial_sl, target_risk_usd)
                    lot_a, lot_b = self.router.split_lot_sizes(broker_sym or sym, lot or 0.02)
                    print(f"   ➔ [Dry-Run Planı]: Toplam {lot} Lot ➔ Bilet A: {lot_a} Lot (+2.0R TP) │ Bilet B: {lot_b} Lot (Runner)")

        if new_signals == 0:
            print("   ℹ️ Bugün için yeni kırılım bulunamadı. Mevcut trendler devam ediyor.")

        print("\n" + "═" * 90)
        print(" 🏁 YAŞAM DÖNGÜSÜ GÖREVİ TAMAMLANDI.")
        print("═" * 90 + "\n")

    def run_daemon_loop(self, target_hour: int = 0, target_minute: int = 10):
        """
        Arka planda kesintisiz çalışır. Her 60 saniyede bir kapanışları kontrol eder,
        gece TSİ 00:10'da ise ana D1 yaşam döngüsünü tetikler.
        """
        print(f"🤖 Matematiksel Bot Daemon Aktif! Her gün saat {target_hour:02d}:{target_minute:02d} TSİ için nöbette...")
        
        last_daily_run_date = None

        while True:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            # 1. Her 60 saniyede bir kapanan işlem var mı diye kontrol et (Telegram bildirimi için)
            try:
                self.check_and_notify_closures()
            except Exception as e:
                logger.error(f"Kapanış denetim hatası: {e}")

            # 2. Gece TSİ 00:10 geldi mi?
            if now.hour == target_hour and now.minute >= target_minute and last_daily_run_date != today_str:
                logger.info("🌙 Gece Rollover Sonrası D1 Yaşam Döngüsü Tetikleniyor...")
                try:
                    self.execute_daily_cycle()
                    last_daily_run_date = today_str
                except Exception as e:
                    logger.error(f"Günlük döngü hatası: {e}")

            time.sleep(60)

    def shutdown(self):
        self.router.shutdown()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Begonya D1 Trade Lifecycle Manager")
    parser.add_argument("--live", action="store_true", help="Canlı MT5 emir iletimini açar")
    parser.add_argument("--daemon", action="store_true", help="TSİ 00:10 için arka plan servis döngüsünü başlatır")
    parser.add_argument("--run-now", action="store_true", help="Beklemeden tek seferlik döngüyü hemen koşturur")
    args = parser.parse_args()

    manager = TradeLifecycleManager(dry_run=not args.live)

    if args.daemon:
        manager.run_daemon_loop()
    else:
        manager.execute_daily_cycle()

    manager.shutdown()
