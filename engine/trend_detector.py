from dataclasses import dataclass
from typing import Optional, Literal
import pandas as pd

@dataclass
class BreakoutSignal:
    timestamp: pd.Timestamp
    symbol: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    initial_sl: float
    atr: float
    risk_distance: float

class TrendDetector:
    def __init__(self, config: dict):
        self.config = config
        self.mech = config.get("mechanics", {})
        self.sl_atr_mult = self.mech.get("sl_atr_multiplier", 1.5)
        self.require_ema200 = self.mech.get("require_ema200_alignment", False)

    def check_breakout(self, df: pd.DataFrame, current_idx: int, symbol: str) -> Optional[BreakoutSignal]:
        """
        D1 barında 20 günlük Donchian zirvesinin veya dibinin kırıldığını tespit eder.
        Sıfır lookahead: Donchian kanalları shift(1) ile dünün barlarından gelir.
        """
        if current_idx < 1:
            return None

        today = df.iloc[current_idx]
        yesterday = df.iloc[current_idx - 1]

        high_20 = today["donchian_high_20"]
        low_20 = today["donchian_low_20"]
        atr = today["atr14"]
        ema200 = today.get("ema200", None)

        if pd.isna(high_20) or pd.isna(low_20) or pd.isna(atr) or atr <= 0:
            return None

        # 1. YUKARI KIRILIM (LONG SİNYALİ)
        # Dün 20 günlük zirvenin altındaydı, bugün zirvenin üzerine taştı
        if yesterday["high"] <= high_20 and today["high"] > high_20:
            # Rejim Filtresi: Eğer aktifse, fiyat 200 EMA'nın üzerinde olmalıdır (Ayı piyasasında sahte long kırılımı alma!)
            if self.require_ema200 and ema200 is not None and not pd.isna(ema200):
                if yesterday["close"] < ema200:
                    return None

            entry = max(today["open"], high_20)
            sl = entry - (self.sl_atr_mult * atr)
            risk = entry - sl
            if risk > 0:
                return BreakoutSignal(
                    timestamp=today.name,
                    symbol=symbol,
                    direction="LONG",
                    entry_price=entry,
                    initial_sl=sl,
                    atr=atr,
                    risk_distance=risk
                )

        # 2. AŞAĞI KIRILIM (SHORT SİNYALİ)
        # Dün 20 günlük dibin üzerindeydi, bugün dibin altına sarktı
        elif yesterday["low"] >= low_20 and today["low"] < low_20:
            # Rejim Filtresi: Fiyat 200 EMA'nın altında olmalıdır (Boğa piyasasında tepeye short açma!)
            if self.require_ema200 and ema200 is not None and not pd.isna(ema200):
                if yesterday["close"] > ema200:
                    return None

            entry = min(today["open"], low_20)
            sl = entry + (self.sl_atr_mult * atr)
            risk = sl - entry
            if risk > 0:
                return BreakoutSignal(
                    timestamp=today.name,
                    symbol=symbol,
                    direction="SHORT",
                    entry_price=entry,
                    initial_sl=sl,
                    atr=atr,
                    risk_distance=risk
                )

        return None
