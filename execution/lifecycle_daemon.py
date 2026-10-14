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
    Bağımsız Matematiksel D1 Convexity Yaşam Döngüsü Yöneticisi:
    1. Dinamik Rotasyon: 16 varlıkta 200 EMA, volatilite (%0.8 ATR) ve karantina filtreleriyle aktif evreni belirler.
    2. Açık Pozisyon Denetimi: Magic 260902 (Runner) biletlerinin stopunu (Aktif: 2.0x ATR, Kırmızı Kart: 1.0x ATR) günceller.
    3. Yeni Sinyal Taraması: Aktif listedeki varlıklarda Donchian 20 + 200 EMA breakout tespit eder (%100 Saf Matematiksel).
    4. İki Bilet İnfazı: %2.0 Sabit Risk ile Bilet A (+2.0R TP) ve Bilet B (Trailing Runner).
    5. Kapanış Takibi & Günlük Nöbet Raporu: Kapanan biletleri ve her gece 00:10 günlük tarama özetini Telegram'a bildirir.
    """

    def __init__(self, shadow_mode: bool = True, dry_run: bool = False):
        self.config = load_config()
        self.telemetry = TelemetryLogger()
        self.router = MT5ExecutionRouter(self.config, telemetry=self.telemetry)
        self.loader = MarketLoader()
        self.detector = TrendDetector(self.config)
        self.rot_engine = RotationalSelectionEngine(self.config)
        self.benchmark_collector = BenchmarkCollector()

        # Sert Gerçek Hesap ve Gölge Modu Koruması:
        blocked, block_reason = self.router.is_real_execution_blocked()
        if blocked:
            self.shadow_mode = True
            logger.warning(f"🛡️ [GÜVENLİK PROTOKOLÜ AKTİF]: {block_reason}")
            logger.warning("👻 [GÖLGE İŞLEM / SHADOW DEVREDE - SIFIR GERÇEK RİSK]")
        else:
            self.shadow_mode = shadow_mode

        self.dry_run = dry_run

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
        """Kapanan gerçek veya gölge biletleri tespit eder, net PnL hesaplar, karantina motoruna kaydeder ve Telegram'a bildirir."""
        if not mt5.initialize():
            return

        tracked = self._load_tracked_tickets()
        if not tracked:
            return

        open_positions = mt5.positions_get()
        open_tickets = {p.ticket for p in open_positions} if open_positions else set()
        closed_tickets = []

        for ticket_str, data in tracked.items():
            is_shadow = data.get("is_shadow", False)
            if is_shadow:
                sym = data.get("symbol", "")
                tick = mt5.symbol_info_tick(sym)
                current_price = None
                direction = data.get("direction", "LONG").upper()
                if tick:
                    current_price = tick.bid if direction in ("LONG", "BUY") else tick.ask
                if current_price is None or current_price <= 0:
                    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 1)
                    if rates is not None and len(rates) > 0:
                        current_price = float(rates[0]["close"])

                if current_price is None or current_price <= 0:
                    continue

                entry_price = float(data.get("entry", 0.0))
                sl_price = float(data.get("sl", 0.0))
                tp_price = float(data.get("tp", 0.0))
                risk_usd = float(data.get("risk_usd", 10.0))
                initial_sl = float(data.get("initial_sl", sl_price))
                ticket_type = data.get("type", "TICKET_A_TP")

                closed = False
                reason = ""
                pnl_r = 0.0
                pnl_dollars = 0.0
                exit_price = current_price

                if direction in ("LONG", "BUY"):
                    if ticket_type == "TICKET_A_TP" and tp_price > 0 and current_price >= tp_price:
                        closed = True
                        exit_price = tp_price
                        pnl_r = 2.0
                        pnl_dollars = round(risk_usd * 2.0, 2)
                        reason = "Hedef Vuruldu (Gölge TP +2.0R)"
                    elif current_price <= sl_price:
                        closed = True
                        exit_price = sl_price
                        if ticket_type == "TICKET_A_TP":
                            pnl_r = -1.0
                            pnl_dollars = round(-risk_usd, 2)
                            reason = "Zarar Kes (Gölge SL -1.0R)"
                        else:
                            risk_dist = abs(entry_price - initial_sl)
                            pnl_r = round((exit_price - entry_price) / risk_dist, 2) if risk_dist > 0 else -1.0
                            pnl_dollars = round(pnl_r * risk_usd, 2)
                            res_tag = "Kârda" if pnl_r > 0 else "Zararda"
                            reason = f"Runner Kapanışı (Gölge Trailing SL: {pnl_r:+.2f}R {res_tag})"
                elif direction in ("SHORT", "SELL"):
                    if ticket_type == "TICKET_A_TP" and tp_price > 0 and current_price <= tp_price:
                        closed = True
                        exit_price = tp_price
                        pnl_r = 2.0
                        pnl_dollars = round(risk_usd * 2.0, 2)
                        reason = "Hedef Vuruldu (Gölge TP +2.0R)"
                    elif current_price >= sl_price:
                        closed = True
                        exit_price = sl_price
                        if ticket_type == "TICKET_A_TP":
                            pnl_r = -1.0
                            pnl_dollars = round(-risk_usd, 2)
                            reason = "Zarar Kes (Gölge SL -1.0R)"
                        else:
                            risk_dist = abs(initial_sl - entry_price)
                            pnl_r = round((entry_price - exit_price) / risk_dist, 2) if risk_dist > 0 else -1.0
                            pnl_dollars = round(pnl_r * risk_usd, 2)
                            res_tag = "Kârda" if pnl_r > 0 else "Zararda"
                            reason = f"Runner Kapanışı (Gölge Trailing SL: {pnl_r:+.2f}R {res_tag})"

                if closed:
                    close_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    generic_sym = data.get("generic_symbol", sym)
                    if pnl_dollars < 0:
                        self.rot_engine.record_trade_loss(generic_sym, datetime.now())
                    else:
                        self.rot_engine.loss_history[generic_sym] = []

                    event = {
                        "status": "CLOSED",
                        "action": "CLOSE",
                        "symbol": sym,
                        "ticket": ticket_str,
                        "is_shadow": True,
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
            else:
                try:
                    ticket_id = int(ticket_str)
                except ValueError:
                    continue
                if ticket_id not in open_tickets:
                    deals = mt5.history_deals_get(position=ticket_id)
                    pnl_dollars = 0.0
                    exit_price = 0.0
                    close_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    if deals:
                        closing_deal = deals[-1]
                        pnl_dollars = round(sum(d.profit + d.commission + d.swap for d in deals), 2)
                        exit_price = closing_deal.price
                        close_time = datetime.fromtimestamp(closing_deal.time).strftime("%Y-%m-%d %H:%M:%S")

                    risk_usd = data.get("risk_usd", 10.0)
                    pnl_r = round(pnl_dollars / risk_usd, 2) if risk_usd > 0 else 0.0
                    reason = "Hedef Vuruldu (TP)" if pnl_dollars > 0 else "Zarar Kes (SL)"

                    generic_sym = data.get("generic_symbol", data.get("symbol", ""))
                    if pnl_dollars < 0:
                        self.rot_engine.record_trade_loss(generic_sym, datetime.now())
                    else:
                        self.rot_engine.loss_history[generic_sym] = []

                    event = {
                        "status": "CLOSED",
                        "action": "CLOSE",
                        "symbol": data.get("symbol", ""),
                        "ticket": ticket_id,
                        "is_shadow": False,
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

        if closed_tickets:
            for t in closed_tickets:
                tracked.pop(t, None)
            self._save_tracked_tickets(tracked)

    def update_shadow_trailing_stops(self, current_d1_data: dict) -> list:
        """Açık olan Gölge Bilet B (Runner) stoplarını ATR kuralıyla kârı koruyacak şekilde günceller."""
        tracked = self._load_tracked_tickets()
        if not tracked:
            return []

        updates = []
        for t_str, data in tracked.items():
            if not data.get("is_shadow", False) or data.get("type") != "TICKET_B_RUNNER":
                continue

            sym = data.get("symbol", "")
            generic_sym = data.get("generic_symbol", sym)
            d1_info = current_d1_data.get(sym) or current_d1_data.get(generic_sym)
            if not d1_info:
                continue

            current_atr = d1_info.get("atr14", 0.0)
            if current_atr <= 0:
                continue

            current_sl = float(data.get("sl", 0.0))
            direction = data.get("direction", "LONG").upper()
            is_active = d1_info.get("is_active", True)
            active_trail_mult = self.router.trailing_atr_mult if is_active else 1.0

            if direction in ("LONG", "BUY"):
                recent_high = d1_info.get("recent_high", d1_info.get("close", data.get("entry", 0.0)))
                new_sl = round(recent_high - (active_trail_mult * current_atr), 5)
                if new_sl > current_sl + (0.1 * current_atr):
                    data["sl"] = new_sl
                    event = {
                        "action": "TRAILING_UPDATE",
                        "ticket": t_str,
                        "symbol": sym,
                        "old_sl": current_sl,
                        "new_sl": new_sl,
                        "status": "SUCCESS",
                        "is_shadow": True,
                        "comment": "Gölge Runner 2.5x ATR Kâr Kilitleme"
                    }
                    self.telemetry.log_execution(event)
                    updates.append(event)
            elif direction in ("SHORT", "SELL"):
                recent_low = d1_info.get("recent_low", d1_info.get("close", data.get("entry", 0.0)))
                new_sl = round(recent_low + (active_trail_mult * current_atr), 5)
                if new_sl < current_sl - (0.1 * current_atr) or current_sl == 0:
                    data["sl"] = new_sl
                    event = {
                        "action": "TRAILING_UPDATE",
                        "ticket": t_str,
                        "symbol": sym,
                        "old_sl": current_sl,
                        "new_sl": new_sl,
                        "status": "SUCCESS",
                        "is_shadow": True,
                        "comment": "Gölge Runner 2.5x ATR Kâr Kilitleme"
                    }
                    self.telemetry.log_execution(event)
                    updates.append(event)

        if updates:
            self._save_tracked_tickets(tracked)
        return updates

    def _get_passive_reasons(self, active_symbols: set, d1_market_cache: dict) -> list:
        reasons = []
        for sym in sorted(self.symbols):
            if sym in active_symbols:
                continue
            info = d1_market_cache.get(sym)
            if not info or info["df"].empty:
                reasons.append((sym, "Veri yetersiz"))
                continue
            row = info["df"].iloc[-1]
            close = float(row["close"])
            ema200 = float(row.get("ema200", close))
            atr = float(row.get("atr14", 0.0))
            atr_pct = (atr / close) * 100.0 if close > 0 else 0.0
            if close <= ema200:
                reasons.append((sym, f"200 EMA Altı ({close:.4f} | EMA: {ema200:.4f})"))
            elif atr_pct < self.rot_engine.min_atr_pct:
                reasons.append((sym, f"Volatilite Sıkışması (ATR %{atr_pct:.2f} / Min %{self.rot_engine.min_atr_pct})"))
            else:
                reasons.append((sym, "30 Günlük Karantina"))
        return reasons

    def execute_daily_cycle(self):
        print("\n" + "═" * 90)
        print(" 🤖 MATEMATİKSEL BOT (BAĞIMSIZ D1 YAŞAM DÖNGÜSÜ) BAŞLADI 🤖 ")
        mode_str = "👻 GÖLGE İŞLEM TAKİBİ / SHADOW (0 Gerçek Risk)" if self.shadow_mode else ("🟡 SİMÜLASYON / DRY-RUN" if self.dry_run else "🔴 CANLI İNFAZ / MT5 LIVE")
        print(f"    Zaman: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Mod: {mode_str} | Saf Matematiksel")
        print("═" * 90)

        # 0. Kapanan bilet denetimi
        self.check_and_notify_closures()

        # Güncel D1 verilerini çek
        d1_market_cache = {}
        for sym in self.symbols:
            df = self.loader.load_symbol_data(sym, period="1y")
            if not df.empty and len(df) >= 50:
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
                    "broker_sym": broker_sym,
                    "is_active": True,
                }
                d1_market_cache[sym] = data_dict
                if broker_sym:
                    d1_market_cache[broker_sym] = data_dict

        # =========================================================================
        # 1. AŞAMA: DİNAMİK ROTASYON VE AKTİF LİSTE BELİRLEME
        # =========================================================================
        print("\n[ADIM 1] 16 Varlıkta Dinamik Rotasyon & Kırmızı Kart Taraması...")
        now = datetime.now()
        active_symbols, audit_events = self.rot_engine.evaluate_and_rebalance(
            current_date=now,
            symbol_data_map={s: d1_market_cache[s]["df"] for s in self.symbols if s in d1_market_cache},
            open_positions=set(self._load_tracked_tickets().keys())
        )
        for sym in self.symbols:
            if sym in d1_market_cache:
                is_act = sym in active_symbols
                d1_market_cache[sym]["is_active"] = is_act
                b_sym = d1_market_cache[sym].get("broker_sym")
                if b_sym and b_sym in d1_market_cache:
                    d1_market_cache[b_sym]["is_active"] = is_act

        passive_reasons = self._get_passive_reasons(active_symbols, d1_market_cache)
        print(f"   🟢 Aktif Listede Olanlar ({len(active_symbols)} Varlık): {', '.join(sorted(list(active_symbols)))}")
        if passive_reasons:
            print(f"   🔴 Kırmızı Kartlı / Pasif ({len(passive_reasons)} Varlık):")
            for s_name, r_txt in passive_reasons:
                print(f"      • {s_name:<8}: {r_txt}")

        # =========================================================================
        # 2. AŞAMA: AÇIK POZİSYON DENETİMİ (TRAILING STOP & GRACEFUL EXIT)
        # =========================================================================
        print("\n[ADIM 2] Açık Runner Biletleri Denetleniyor (Aktif: 2.0x ATR | Kırmızı Kart: 1.0x ATR)...")
        if self.shadow_mode:
            trail_updates = self.update_shadow_trailing_stops(d1_market_cache)
            if trail_updates:
                print(f"   ✅ {len(trail_updates)} adet gölge pozisyonun stop seviyesi kârı koruyacak şekilde taşındı.")
            else:
                print("   ℹ️ Gölge stop seviyesini taşımayı gerektirecek yeni bir zirve/dip oluşmadı.")
        elif not self.dry_run:
            trail_updates = self.router.update_runner_trailing_stops(d1_market_cache)
            if trail_updates:
                print(f"   ✅ {len(trail_updates)} adet canlı pozisyonun stop seviyesi yukarı taşındı.")
            else:
                print("   ℹ️ Stop seviyesini yukarı taşımayı gerektirecek yeni bir zirve/dip oluşmadı.")
        else:
            print("   ℹ️ [Dry-Run] Açık pozisyon stop kontrolü simüle edildi.")

        # =========================================================================
        # 3. AŞAMA: YENİ SİNYAL TARAMASI VE İKİ BİLET İNFAZI (%100 SAF MATEMATİKSEL)
        # =========================================================================
        print("\n[ADIM 3] Aktif Varlıklarda D1 Donchian 20 + 200 EMA Kırılımları Taranıyor...")
        executed_signals = []
        radar_list = []
        tracked_tickets = self._load_tracked_tickets()

        # MT5 Hesap Bakiyesini Al ve %2.0 Sabit Risk Hesapla
        acc = mt5.account_info() if mt5.initialize() else None
        balance = acc.balance if acc else self.config.get("backtest", {}).get("initial_capital", 10000.0)
        risk_pct = self.config.get("execution", {}).get("risk_per_trade_pct", 2.0) / 100.0
        target_risk_usd = round(balance * risk_pct, 2)

        for sym in self.symbols:
            if sym not in d1_market_cache or sym not in active_symbols:
                continue

            df = d1_market_cache[sym]["df"]
            latest_idx = len(df) - 1
            sig = self.detector.check_breakout(df, latest_idx, sym)

            if sig:
                broker_sym = d1_market_cache[sym].get("broker_sym")
                print(f"\n⚡ [MATEMATİKSEL KIRILIM ONAYLANDI] {sig.direction} {sym} ({broker_sym}) @ {sig.entry_price:.4f} | SL: {sig.initial_sl:.4f}")

                # Mükerrer İşlem Koruması (State Lock - Canlı MT5 + Gölge Biletler)
                has_open_pos = False
                if broker_sym:
                    existing = self.router.get_open_positions_for_symbol(broker_sym)
                    if existing:
                        has_open_pos = True
                if any(t.get("generic_symbol") == sym or t.get("symbol") == broker_sym for t in tracked_tickets.values()):
                    has_open_pos = True

                if has_open_pos:
                    print(f"   ⚠️ [STATE LOCK]: Bu sembolde zaten açık pozisyon/gölge bilet var. Çift işlem kilitlendi!")
                    continue

                mode_exec_str = "GÖLGE İNFAZ" if self.shadow_mode else "SAF MATEMATİKSEL İNFAZ"
                print(f"   ✅ [{mode_exec_str}]: Bakiye: ${balance:,.2f} | Sabit Risk: %{risk_pct*100:.1f} (${target_risk_usd:,.2f})")
                executed_signals.append(sig)

                if self.shadow_mode or not self.dry_run:
                    print(f"   ➔ İki Bilet (Split Ticket) Hazırlanıyor: Bilet A (+2.0R TP) + Bilet B (Runner)...")
                    res = self.router.execute_split_tickets(sym, sig.direction, sig.entry_price, sig.initial_sl, target_risk_usd)
                    print(f"   ➔ İnfaz Durumu: {res.get('status')} | Bilet A: #{res.get('ticket_a')} | Bilet B: #{res.get('ticket_b')}")

                    is_virtual = res.get("is_shadow", self.shadow_mode)
                    if res.get("ticket_a"):
                        tracked_tickets[str(res["ticket_a"])] = {
                            "symbol": broker_sym or sym,
                            "generic_symbol": sym,
                            "type": "TICKET_A_TP",
                            "direction": sig.direction,
                            "risk_usd": target_risk_usd / 2.0,
                            "entry": float(res.get("avg_fill_price", sig.entry_price)),
                            "sl": float(res.get("sl", sig.initial_sl)),
                            "initial_sl": float(sig.initial_sl),
                            "tp": float(res.get("tp_a", 0.0)),
                            "lot": float(res.get("lot_a", 0.01)),
                            "is_shadow": is_virtual,
                            "open_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                    if res.get("ticket_b"):
                        tracked_tickets[str(res["ticket_b"])] = {
                            "symbol": broker_sym or sym,
                            "generic_symbol": sym,
                            "type": "TICKET_B_RUNNER",
                            "direction": sig.direction,
                            "risk_usd": target_risk_usd / 2.0,
                            "entry": float(res.get("avg_fill_price", sig.entry_price)),
                            "sl": float(res.get("sl", sig.initial_sl)),
                            "initial_sl": float(sig.initial_sl),
                            "tp": 0.0,
                            "lot": float(res.get("lot_b", 0.01)),
                            "is_shadow": is_virtual,
                            "open_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                    self._save_tracked_tickets(tracked_tickets)
                else:
                    lot = self.router.calculate_dynamic_lot(broker_sym or sym, sig.entry_price, sig.initial_sl, target_risk_usd)
                    lot_a, lot_b = self.router.split_lot_sizes(broker_sym or sym, lot or 0.02)
                    print(f"   ➔ [Dry-Run Planı]: Toplam {lot} Lot ➔ Bilet A: {lot_a} Lot (+2.0R TP) │ Bilet B: {lot_b} Lot (Runner)")
            else:
                # Kırılıma yakın olanları radara ekle (%1.5 mesafe)
                row = df.iloc[-1]
                close_p = float(row["close"])
                high_20 = float(row["donchian_high_20"])
                dist_pct = ((high_20 - close_p) / close_p) * 100.0 if close_p > 0 else 99.0
                if 0 < dist_pct <= 1.5:
                    radar_list.append(f"{sym} (%{dist_pct:.1f})")

        if not executed_signals:
            print("   ℹ️ Bugün için yeni kırılım bulunamadı. Mevcut trendler devam ediyor.")

        # =========================================================================
        # 4. AŞAMA: GÜNLÜK TELEGRAM NÖBET ÖZETİ (HEARTBEAT)
        # =========================================================================
        try:
            active_str = ", ".join(sorted(list(active_symbols))) if active_symbols else "Yok"
            passive_str = "\n".join([f"• <b>{s}</b>: {r}" for s, r in passive_reasons]) if passive_reasons else "Yok"
            if executed_signals:
                sig_title = "👻 <b>BUGÜN AÇILAN GÖLGE İŞLEMLER</b>" if self.shadow_mode else "🚨 <b>BUGÜN AÇILAN İŞLEMLER</b>"
                sig_str = f"{sig_title} ({len(executed_signals)}):\n"
                for s in executed_signals:
                    sig_str += f"➔ <b>{s.direction} {s.symbol}</b> @ {s.entry_price:.4f} (SL: {s.initial_sl:.4f})\n"
            else:
                sig_str = "ℹ️ Bugün yeni kırılım sinyali yok (Açık pozisyonlar ve aktif trendler izleniyor)."

            radar_str = f"\n👀 <b>Kırılıma Yakın (Radar):</b> {', '.join(radar_list)}" if radar_list else ""
            
            # Açık gölge bilet sayısı
            shadow_count = sum(1 for t in tracked_tickets.values() if t.get("is_shadow", False))
            mode_badge = f"👻 GÖLGE İŞLEM TAKİBİ (Açık: {shadow_count} Bilet)" if self.shadow_mode else "🔴 CANLI MT5 İNFAZ"

            tg_summary = (
                f"🤖 <b>MATEMATİKSEL BOT │ GÜNLÜK NÖBET RAPORU</b> 🤖\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ <b>Mod:</b> {mode_badge}\n"
                f"🛡️ <b>Hesap Güvenliği:</b> The 5%ers Gerçek Hesap Koruması AKTİF (Sıfır Gerçek Risk)\n"
                f"💰 <b>MT5 Bakiye:</b> ${balance:,.2f} │ <b>Sanal Risk:</b> %{risk_pct*100:.1f} (${target_risk_usd:,.2f}/1R)\n"
                f"⏰ <b>Zaman:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🟢 <b>Aktif Evren ({len(active_symbols)}):</b>\n<code>{active_str}</code>\n\n"
                f"🔴 <b>Kırmızı Kart / Pasif ({len(passive_reasons)}):</b>\n{passive_str}\n\n"
                f"{sig_str}{radar_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <i>7/24 Kesintisiz Gölge Nöbet Aktif (Her gece 00:10 tarama + 60sn sanal bilet takip).</i>"
            )
            self.telemetry.send_custom_message(tg_summary)
            print("   📲 Günlük Nöbet Raporu Telegram'a (@Begonyamatematiksel_bot) iletildi.")
        except Exception as e:
            logger.error(f"Günlük Telegram özeti gönderilemedi: {e}")

        print("\n" + "═" * 90)
        print(" 🏁 YAŞAM DÖNGÜSÜ GÖREVİ TAMAMLANDI.")
        print("═" * 90 + "\n")

    def run_daemon_loop(self, target_hour: int = 0, target_minute: int = 10, run_immediately: bool = False):
        """
        Arka planda kesintisiz çalışır. Her 60 saniyede bir kapanışları kontrol eder,
        gece TSİ 00:10'da ise ana D1 yaşam döngüsünü tetikler.
        """
        mode_desc = "GÖLGE İŞLEM (SHADOW)" if self.shadow_mode else "CANLI"
        print(f"🤖 Bağımsız Matematiksel Bot Daemon Aktif [{mode_desc}]! Her gün saat {target_hour:02d}:{target_minute:02d} TSİ için nöbette...")
        
        last_daily_run_date = None
        if run_immediately:
            try:
                self.execute_daily_cycle()
                last_daily_run_date = datetime.now().strftime("%Y-%m-%d")
            except Exception as e:
                logger.error(f"Başlangıç döngü hatası: {e}")

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
    parser = argparse.ArgumentParser(description="Bağımsız Matematiksel D1 Trade Lifecycle Manager")
    parser.add_argument("--live", action="store_true", help="Canlı MT5 emir iletimini açmayı dener (Gerçek hesap yoksa)")
    parser.add_argument("--shadow", action="store_true", default=True, help="Gölge / Sanal işlem takibi modunu açar")
    parser.add_argument("--daemon", action="store_true", help="TSİ 00:10 için arka plan servis döngüsünü başlatır")
    parser.add_argument("--run-now", action="store_true", help="Daemon başlarken ilk taramayı hemen koşturur")
    args = parser.parse_args()

    # Eğer --live verilse bile gerçek hesap bağlıysa sistem sert güvenlik kilidiyle shadow'a çeker
    use_shadow = True
    if args.live:
        use_shadow = False

    manager = TradeLifecycleManager(shadow_mode=use_shadow)

    if args.daemon:
        manager.run_daemon_loop(run_immediately=args.run_now)
    else:
        manager.execute_daily_cycle()

    manager.shutdown()
