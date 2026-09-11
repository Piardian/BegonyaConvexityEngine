from dataclasses import dataclass
from typing import Optional, List, Dict
import pandas as pd
from engine.trend_detector import BreakoutSignal

@dataclass
class ConvexityTrade:
    entry_date: str
    exit_date: str
    symbol: str
    direction: str
    entry_price: float
    initial_sl: float
    exit_price: float
    holding_days: int
    risk_dollars: float
    pnl_dollars: float
    pnl_r: float
    exit_reason: str
    peak_r: float
    partial_taken: bool

class ConvexitySimulator:
    def __init__(self, config: dict):
        self.config = config
        self.mech = config.get("mechanics", {})
        self.backtest_cfg = config.get("backtest", {})
        self.initial_capital = self.backtest_cfg.get("initial_capital", 10000.0)
        self.risk_pct = self.backtest_cfg.get("risk_per_trade_pct", 0.5) / 100.0
        self.be_trigger_r = self.mech.get("be_trigger_r", 2.0)
        self.partial_tp_r = self.mech.get("partial_tp_r", 2.0)
        self.partial_ratio = self.mech.get("partial_close_ratio", 0.50)
        self.trailing_atr_mult = self.mech.get("trailing_atr_multiplier", 2.0)

    def simulate_trades_for_symbol(self, df: pd.DataFrame, signals: List[BreakoutSignal], symbol: str) -> List[ConvexityTrade]:
        """
        Bir sembol için üretilen sinyalleri parçalı kâr alma (scaling-out) ve izleyen stopla simüle eder.
        1. Kademe: +2.0R'da %50 kâr al (1.0R kasada kilitlenir). Stop maliyetin üstüne çekilir.
        2. Kademe: Kalan %50 pozisyon 2.0x ATR trailing stop ile trend bittiği kanıtlanana kadar koşturulur.
        """
        trades: List[ConvexityTrade] = []
        in_trade = False
        current_trade = None

        sig_map = {s.timestamp: s for s in signals}

        for i in range(len(df)):
            curr_date = df.index[i]
            curr_bar = df.iloc[i]

            # 1. Aktif Pozisyonu Yönet
            if in_trade and current_trade is not None:
                holding_days = (curr_date - current_trade["entry_time"]).days
                direction = current_trade["direction"]
                entry = current_trade["entry_price"]
                sl = current_trade["current_sl"]
                risk_dist = current_trade["risk_distance"]
                atr = curr_bar["atr14"]

                # En yüksek R zirvesini güncelle
                if direction == "LONG":
                    max_high = max(current_trade["max_extreme"], curr_bar["high"])
                    current_trade["max_extreme"] = max_high
                    peak_r = (max_high - entry) / risk_dist

                    # A) PARÇALI KÂR ALMA (%50 Kâr Kilitleme & Breakeven)
                    if peak_r >= self.partial_tp_r and not current_trade["partial_taken"]:
                        current_trade["partial_taken"] = True
                        current_trade["realized_r"] = self.partial_ratio * self.partial_tp_r # 0.5 * 2.0 = +1.0R cepte!
                        current_trade["remaining_ratio"] = 1.0 - self.partial_ratio
                        # Kalan kısmın stopunu maliyetin biraz üstüne çek
                        sl = max(sl, entry + (0.05 * risk_dist))
                        current_trade["current_sl"] = sl

                    # B) TRAILING STOP (Kalan %50 için izleyen stop)
                    if current_trade["partial_taken"]:
                        trailing_sl = max_high - (self.trailing_atr_mult * atr)
                        if trailing_sl > sl:
                            sl = trailing_sl
                            current_trade["current_sl"] = sl

                    # C) ÇIKIŞ KONTROLÜ
                    if curr_bar["low"] <= sl:
                        exit_price = min(curr_bar["open"], sl)
                        runner_dist = exit_price - entry
                        runner_r = runner_dist / risk_dist

                        if current_trade["partial_taken"]:
                            # Toplam PnL = Kilitlenen 1.0R + (0.50 * runner_r)
                            total_pnl_r = round(current_trade["realized_r"] + (current_trade["remaining_ratio"] * runner_r), 2)
                            exit_reason = "Trailing Runner TP" if runner_r > 0 else "Partial Lock + BE"
                        else:
                            total_pnl_r = round(runner_r, 2)
                            exit_reason = "Stop Loss"

                        pnl_dollars = round(total_pnl_r * current_trade["risk_dollars"], 2)

                        trades.append(ConvexityTrade(
                            entry_date=str(current_trade["entry_time"].date()),
                            exit_date=str(curr_date.date()),
                            symbol=symbol,
                            direction=direction,
                            entry_price=entry,
                            initial_sl=current_trade["initial_sl"],
                            exit_price=exit_price,
                            holding_days=holding_days,
                            risk_dollars=current_trade["risk_dollars"],
                            pnl_dollars=pnl_dollars,
                            pnl_r=total_pnl_r,
                            exit_reason=exit_reason,
                            peak_r=round(peak_r, 2),
                            partial_taken=current_trade["partial_taken"]
                        ))
                        in_trade = False
                        current_trade = None
                        continue

                elif direction == "SHORT":
                    min_low = min(current_trade["max_extreme"], curr_bar["low"])
                    current_trade["max_extreme"] = min_low
                    peak_r = (entry - min_low) / risk_dist

                    # A) PARÇALI KÂR ALMA
                    if peak_r >= self.partial_tp_r and not current_trade["partial_taken"]:
                        current_trade["partial_taken"] = True
                        current_trade["realized_r"] = self.partial_ratio * self.partial_tp_r
                        current_trade["remaining_ratio"] = 1.0 - self.partial_ratio
                        sl = min(sl, entry - (0.05 * risk_dist))
                        current_trade["current_sl"] = sl

                    # B) TRAILING STOP
                    if current_trade["partial_taken"]:
                        trailing_sl = min_low + (self.trailing_atr_mult * atr)
                        if trailing_sl < sl:
                            sl = trailing_sl
                            current_trade["current_sl"] = sl

                    # C) ÇIKIŞ KONTROLÜ
                    if curr_bar["high"] >= sl:
                        exit_price = max(curr_bar["open"], sl)
                        runner_dist = entry - exit_price
                        runner_r = runner_dist / risk_dist

                        if current_trade["partial_taken"]:
                            total_pnl_r = round(current_trade["realized_r"] + (current_trade["remaining_ratio"] * runner_r), 2)
                            exit_reason = "Trailing Runner TP" if runner_r > 0 else "Partial Lock + BE"
                        else:
                            total_pnl_r = round(runner_r, 2)
                            exit_reason = "Stop Loss"

                        pnl_dollars = round(total_pnl_r * current_trade["risk_dollars"], 2)

                        trades.append(ConvexityTrade(
                            entry_date=str(current_trade["entry_time"].date()),
                            exit_date=str(curr_date.date()),
                            symbol=symbol,
                            direction=direction,
                            entry_price=entry,
                            initial_sl=current_trade["initial_sl"],
                            exit_price=exit_price,
                            holding_days=holding_days,
                            risk_dollars=current_trade["risk_dollars"],
                            pnl_dollars=pnl_dollars,
                            pnl_r=total_pnl_r,
                            exit_reason=exit_reason,
                            peak_r=round(peak_r, 2),
                            partial_taken=current_trade["partial_taken"]
                        ))
                        in_trade = False
                        current_trade = None
                        continue

            # 2. Yeni Sinyal Varsa İşleme Gir
            if not in_trade and curr_date in sig_map:
                sig = sig_map[curr_date]
                risk_dollars = self.initial_capital * self.risk_pct
                current_trade = {
                    "entry_time": curr_date,
                    "direction": sig.direction,
                    "entry_price": sig.entry_price,
                    "initial_sl": sig.initial_sl,
                    "current_sl": sig.initial_sl,
                    "risk_distance": sig.risk_distance,
                    "risk_dollars": risk_dollars,
                    "max_extreme": sig.entry_price,
                    "partial_taken": False,
                    "realized_r": 0.0,
                    "remaining_ratio": 1.0
                }
                in_trade = True

        return trades
