import time
import math
import logging
from typing import Dict, Any, Optional, List, Tuple
import MetaTrader5 as mt5

from execution.symbol_mapper import SymbolMapper
from execution.telemetry_logger import TelemetryLogger

logger = logging.getLogger("MT5ExecutionRouter")

class MT5ExecutionRouter:
    """
    MetaTrader 5 Tam Otomatik İnfaz & Yaşam Döngüsü Yönlendiricisi.
    - İki Biletli (Split-Ticket) İnfaz Mimarisi:
        * Bilet A (%50 Hacim): Sabit +2.0R Take Profit (Limit Emir Koruması)
        * Bilet B (%50 Hacim): TP Yok (2.5x ATR Trailing Stop ile Koşturulur)
    - Dinamik Lot ve Tick/Contract Size Normalizasyonu
    - Rollover & Spread Spike Filtresi
    - Canlı Pozisyon Denetimi ve Trailing SL Güncellemesi (TRADE_ACTION_SLTP)
    - Slippage (Kayma Delta) Ölçümü ve Telemetri
    """

    MAGIC_SCALER = 260901  # Bilet A (+2.0R TP)
    MAGIC_RUNNER = 260902  # Bilet B (Trailing Runner)

    def __init__(self, config: dict, telemetry: Optional[TelemetryLogger] = None):
        self.config = config
        self.risk_pct = config.get("execution", {}).get("risk_per_trade_pct", 0.10) / 100.0
        self.max_spread_multiplier = config.get("execution", {}).get("max_spread_multiplier", 2.0)
        self.trailing_atr_mult = config.get("mechanics", {}).get("trailing_atr_multiplier", 2.5)
        self.mapper = SymbolMapper(config.get("execution", {}).get("symbol_overrides", {}))
        self.telemetry = telemetry or TelemetryLogger()

        self._init_mt5()

    def _init_mt5(self) -> bool:
        if not mt5.initialize():
            logger.error(f"MT5 başlatılamadı: {mt5.last_error()}")
            return False
        
        term = mt5.terminal_info()
        if term and not term.trade_allowed:
            msg = "🚨 DİKKAT: MT5 terminalinde 'Algo Trading' (Otomatik Al-Sat) butonu KAPALI! Terminal üst menüsünden butona basarak yeşile çevirin (veya Ctrl+E)."
            logger.warning(msg)
            print("\n" + "!" * 90)
            print(f" {msg}")
            print("!" * 90 + "\n")
        return True

    def calculate_dynamic_lot(self, broker_symbol: str, entry_price: float, sl_price: float, target_risk_usd: float) -> Optional[float]:
        """Her enstrümanın sözleşme büyüklüğüne göre toplam 1R lotunu hesaplar."""
        info = mt5.symbol_info(broker_symbol)
        if not info:
            return None

        price_dist = abs(entry_price - sl_price)
        tick_size = info.trade_tick_size if info.trade_tick_size > 0 else info.point
        tick_value = info.trade_tick_value

        if tick_size <= 0 or tick_value <= 0:
            return None

        ticks_at_risk = price_dist / tick_size
        loss_per_lot_usd = ticks_at_risk * tick_value

        if loss_per_lot_usd <= 0:
            return None

        raw_lots = target_risk_usd / loss_per_lot_usd
        step = info.volume_step
        if step > 0:
            normalized_lots = math.floor(raw_lots / step) * step
            decimals = int(round(-math.log10(step))) if step < 1 else 0
            normalized_lots = round(normalized_lots, decimals)
        else:
            normalized_lots = round(raw_lots, 2)

        return max(info.volume_min, min(info.volume_max, normalized_lots))

    def split_lot_sizes(self, broker_symbol: str, total_lot: float) -> Tuple[float, float]:
        """
        Toplam lotu Bilet A (%50 TP) ve Bilet B (%50 Runner) için böler.
        Minimum hacim sınırına (volume_min) ve step kuralına tam uyar.
        """
        info = mt5.symbol_info(broker_symbol)
        min_vol = info.volume_min if info else 0.01
        step = info.volume_step if info else 0.01

        half = total_lot / 2.0
        if step > 0:
            lot_a = math.floor(half / step) * step
            lot_b = total_lot - lot_a
            decimals = int(round(-math.log10(step))) if step < 1 else 0
            lot_a = round(max(min_vol, lot_a), decimals)
            lot_b = round(max(min_vol, lot_b), decimals)
        else:
            lot_a = max(min_vol, round(half, 2))
            lot_b = max(min_vol, round(total_lot - lot_a, 2))

        return lot_a, lot_b

    def check_spread_safety(self, broker_symbol: str) -> bool:
        """Gece yarısı rollover spread patlamasını kontrol eder."""
        info = mt5.symbol_info(broker_symbol)
        if not info:
            return False

        current_spread = info.spread
        max_allowed = self.config.get("execution", {}).get("max_allowed_spread_points", {}).get(broker_symbol, 250)
        if current_spread > max_allowed:
            logger.warning(f"🚨 SPREAD PATLAMASI [{broker_symbol}]: {current_spread} pts > {max_allowed} pts.")
            return False
        return True

    def get_open_positions_for_symbol(self, broker_symbol: str) -> List[Any]:
        """Sembolde açık pozisyonları sorgular (Magic kontrolüyle)."""
        positions = mt5.positions_get(symbol=broker_symbol)
        if not positions:
            return []
        return [p for p in positions if p.magic in (self.MAGIC_SCALER, self.MAGIC_RUNNER)]

    def execute_split_tickets(self, generic_symbol: str, direction: str, entry_price: float, sl_price: float, target_risk_usd: float) -> Dict[str, Any]:
        """
        İki Biletli (Split-Ticket) İnfaz:
        1. Bilet A: %50 Lot, TP = +2.0R, SL = initial_sl, Magic = MAGIC_SCALER
        2. Bilet B: %50 Lot, TP = Yok, SL = initial_sl, Magic = MAGIC_RUNNER
        """
        broker_symbol = self.mapper.resolve(generic_symbol)
        if not broker_symbol:
            return {"status": "ERROR", "reason": f"Sembol MT5'te bulunamadı: {generic_symbol}"}

        # 1. Rollover Spread Kontrolü
        if not self.check_spread_safety(broker_symbol):
            return {"status": "REJECTED_SPREAD", "symbol": broker_symbol}

        # 2. State Machine: Halihazırda açık pozisyon varsa çakışmayı önle
        existing = self.get_open_positions_for_symbol(broker_symbol)
        if existing:
            return {"status": "SKIPPED_EXISTING", "symbol": broker_symbol, "reason": "Zaten açık pozisyon mevcut"}

        total_lot = self.calculate_dynamic_lot(broker_symbol, entry_price, sl_price, target_risk_usd)
        if not total_lot or total_lot <= 0:
            return {"status": "ERROR", "symbol": broker_symbol, "reason": "Geçersiz lot boyutu"}

        lot_a, lot_b = self.split_lot_sizes(broker_symbol, total_lot)

        tick = mt5.symbol_info_tick(broker_symbol)
        if not tick:
            return {"status": "ERROR", "symbol": broker_symbol, "reason": "Tick verisi yok"}

        order_type = mt5.ORDER_TYPE_BUY if direction.upper() == "LONG" else mt5.ORDER_TYPE_SELL
        req_price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

        # Fiyat Skalası Uyuşmazlık Denetimi (Örn: Petrol CFD vs Hisse Senedi ETF)
        price_mismatch = abs(req_price - entry_price) / max(entry_price, 1e-5)
        if price_mismatch > 0.08:
            err_msg = f"Fiyat Uyuşmazlığı ({generic_symbol} -> {broker_symbol}): Sinyal={entry_price:.2f}, MT5={req_price:.2f}"
            logger.error(err_msg)
            res = {"status": "REJECTED_PRICE_MISMATCH", "symbol": broker_symbol, "action": direction, "reason": err_msg}
            self.telemetry.log_execution(res)
            return res

        risk_dist = abs(entry_price - sl_price)
        # SL ve TP'yi doğrudan gerçekleşecek infaz fiyatına (req_price) sabitle!
        if order_type == mt5.ORDER_TYPE_BUY:
            order_sl = round(req_price - risk_dist, 5)
            order_tp_a = round(req_price + (2.0 * risk_dist), 5)
        else:
            order_sl = round(req_price + risk_dist, 5)
            order_tp_a = round(req_price - (2.0 * risk_dist), 5)

        # Dinamik Filling Mode Belirleme
        info = mt5.symbol_info(broker_symbol)
        filling_mode = mt5.ORDER_FILLING_FOK
        if info and (info.filling_mode & 2):
            filling_mode = mt5.ORDER_FILLING_IOC
        elif info and (info.filling_mode & 1):
            filling_mode = mt5.ORDER_FILLING_FOK
        else:
            filling_mode = mt5.ORDER_FILLING_RETURN

        # --- BİLET A GÖNDER (+2.0R Limit TP) ---
        req_a = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_symbol,
            "volume": float(lot_a),
            "type": order_type,
            "price": req_price,
            "sl": float(order_sl),
            "tp": float(order_tp_a),
            "deviation": 20,
            "magic": self.MAGIC_SCALER,
            "comment": "Begonya TP 2.0R",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }
        res_a = mt5.order_send(req_a)

        # --- BİLET B GÖNDER (Trailing Runner - Sonsuz) ---
        req_b = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_symbol,
            "volume": float(lot_b),
            "type": order_type,
            "price": req_price,
            "sl": float(order_sl),
            "tp": 0.0, # TP Yok!
            "deviation": 20,
            "magic": self.MAGIC_RUNNER,
            "comment": "Begonya Runner",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }
        res_b = mt5.order_send(req_b)

        if res_a is None or res_a.retcode != mt5.TRADE_RETCODE_DONE:
            logger.warning(f"⚠️ Bilet A İnfaz Edilemedi [{broker_symbol}]: Retcode={res_a.retcode if res_a else 'None'} ({res_a.comment if res_a else mt5.last_error()})")
        if res_b is None or res_b.retcode != mt5.TRADE_RETCODE_DONE:
            logger.warning(f"⚠️ Bilet B İnfaz Edilemedi [{broker_symbol}]: Retcode={res_b.retcode if res_b else 'None'} ({res_b.comment if res_b else mt5.last_error()})")

        # Telemetri ve Kayma Hesaplama
        success_a = res_a is not None and res_a.retcode == mt5.TRADE_RETCODE_DONE
        success_b = res_b is not None and res_b.retcode == mt5.TRADE_RETCODE_DONE

        fill_a = res_a.price if success_a else req_price
        fill_b = res_b.price if success_b else req_price
        avg_fill = (fill_a + fill_b) / 2.0

        info = mt5.symbol_info(broker_symbol)
        point = info.point if info else 0.0001
        slip_pts = round(((avg_fill - req_price) if order_type == mt5.ORDER_TYPE_BUY else (req_price - avg_fill)) / point, 2)
        slip_r = round(abs(avg_fill - req_price) / risk_dist, 4)

        if success_a and success_b:
            exec_status = "FILLED"
            fail_reason = "OK"
        else:
            exec_status = "FAILED"
            comment_a = res_a.comment if res_a else "None"
            comment_b = res_b.comment if res_b else "None"
            fail_reason = f"Bilet A: {comment_a} | Bilet B: {comment_b}"

        event = {
            "status": exec_status,
            "symbol": broker_symbol,
            "action": direction,
            "ticket_a": res_a.order if success_a else None,
            "lot_a": lot_a,
            "tp_a": order_tp_a,
            "ticket_b": res_b.order if success_b else None,
            "lot_b": lot_b,
            "sl": order_sl,
            "requested_price": req_price,
            "avg_fill_price": avg_fill,
            "slippage_pts": slip_pts,
            "slippage_r": slip_r,
            "spread": info.spread if info else 0,
            "reason": fail_reason
        }
        self.telemetry.log_execution(event)
        return event

    def update_runner_trailing_stops(self, current_d1_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Açık olan Bilet B (Runner) pozisyonlarının stoplarını 2.5x ATR kuralıyla günceller.
        mt5.order_send(TRADE_ACTION_SLTP) ile stop seviyesini revize eder.
        """
        positions = mt5.positions_get()
        if not positions:
            return []

        runner_positions = [p for p in positions if p.magic == self.MAGIC_RUNNER]
        results = []

        for pos in runner_positions:
            sym = pos.symbol
            d1_info = current_d1_data.get(sym)
            if not d1_info:
                continue

            current_atr = d1_info.get("atr14", 0.0)
            if current_atr <= 0:
                continue

            current_sl = pos.sl
            entry_price = pos.price_open
            pos_type = pos.type # 0: BUY, 1: SELL

            if pos_type == mt5.POSITION_TYPE_BUY:
                recent_high = d1_info.get("recent_high", d1_info.get("close", entry_price))
                new_sl = round(recent_high - (self.trailing_atr_mult * current_atr), 5)

                # Stop yalnızca yukarı yönlü revize edilir (Kârı kilitlemek için)
                if new_sl > current_sl + (0.1 * current_atr):
                    req = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": pos.ticket,
                        "symbol": sym,
                        "sl": float(new_sl),
                        "tp": float(pos.tp),
                    }
                    res = mt5.order_send(req)
                    is_ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
                    event = {
                        "action": "TRAILING_UPDATE",
                        "ticket": pos.ticket,
                        "symbol": sym,
                        "old_sl": current_sl,
                        "new_sl": new_sl,
                        "status": "SUCCESS" if is_ok else "FAILED",
                        "comment": res.comment if res else "Error"
                    }
                    self.telemetry.log_execution(event)
                    results.append(event)

            elif pos_type == mt5.POSITION_TYPE_SELL:
                recent_low = d1_info.get("recent_low", d1_info.get("close", entry_price))
                new_sl = round(recent_low + (self.trailing_atr_mult * current_atr), 5)

                # Stop yalnızca aşağı yönlü revize edilir
                if new_sl < current_sl - (0.1 * current_atr) or current_sl == 0:
                    req = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": pos.ticket,
                        "symbol": sym,
                        "sl": float(new_sl),
                        "tp": float(pos.tp),
                    }
                    res = mt5.order_send(req)
                    is_ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
                    event = {
                        "action": "TRAILING_UPDATE",
                        "ticket": pos.ticket,
                        "symbol": sym,
                        "old_sl": current_sl,
                        "new_sl": new_sl,
                        "status": "SUCCESS" if is_ok else "FAILED",
                        "comment": res.comment if res else "Error"
                    }
                    self.telemetry.log_execution(event)
                    results.append(event)

        return results

    def shutdown(self):
        mt5.shutdown()
