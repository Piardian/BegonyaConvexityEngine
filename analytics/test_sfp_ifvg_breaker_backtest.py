"""
🤖 Begonya Convexity Engine (Matematiksel Bot)
SFP + iFVG (%50 CE) + Yapısal Breaker + 4H Pro-Trend (AAA) Stratejisi Doğrulama ve Karşılaştırma Testi

Bu script, asıl Begonya projesine hiçbir şekilde dokunmadan,
SFP + iFVG + Breaker stratejisini:
1) Begonya'daki 1 Aylık (custom_1m_backtest.py) Makro Kapılı Kurgu ile birebir doğrular (SFP-Only vs SFP+ORB).
2) Matematiksel Bot'un Saf Matematiksel (Makro Kapısız, 4H EMA50/200 AAA Trend + D1 Rotasyon) kurgusuyla 1 Aylık ve 3 Aylık periyotta test eder.
3) Ayrıca 1 Dakikalık (M1 Timeframe) mumlar üzerinde de aynı kurguyu test ederek '1 Aylık (M15)' ile '1 Dakikalık (M1)' farkını şeffafça raporlar.
"""

import sys
import json
import logging
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np
import MetaTrader5 as mt5
import yfinance as yf

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("Convexity.SFP_iFVG_Test")

# Tier-1 Kurumsal Evren (Altcoinler ve Gürültülü Çaprazlar Çıkarılmış)
TIER1_SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCAD", "NZDUSD", "EURGBP",
    "XAUUSD", "NAS100", "BTCUSD"
]

FOREX_SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCAD", "NZDUSD", "EURGBP"
]

ASSET_SL_BUFFER_MAP = {
    "XAUUSD": 0.35,
    "BTCUSD": 0.35,
    "NAS100": 0.22,
    "EURGBP": 0.20,
    "EURUSD": 0.15,
    "GBPUSD": 0.18,
    "USDCAD": 0.18,
    "USDJPY": 0.20,
    "NZDUSD": 0.18,
}


def get_sl_buffer(symbol: str) -> float:
    clean = symbol.replace("/", "").replace("-", "").upper()
    return ASSET_SL_BUFFER_MAP.get(clean, 0.20)


def is_crypto(symbol: str) -> bool:
    return "BTC" in symbol.upper() or "ETH" in symbol.upper() or "SOL" in symbol.upper()


# ============================================================================
# ENTRY & YAPI MEKANİKLERİ (iFVG %50 CE + BREAKER + SLICE-THROUGH)
# ============================================================================

def detect_ifvg_short(i: int, highs, lows, closes, c_atr: float, lookback: int = 30, min_atr_mult: float = 0.08) -> Optional[Tuple[float, float]]:
    for k in range(i - 1, max(2, i - lookback), -1):
        if lows[k] > highs[k - 2]:
            gap_size = float(lows[k] - highs[k - 2])
            if gap_size >= c_atr * min_atr_mult:
                fvg_top = float(lows[k])
                fvg_bot = float(highs[k - 2])
                if closes[i] < fvg_bot and closes[k] >= fvg_bot:
                    return (fvg_top, fvg_bot)
    return None


def detect_ifvg_long(i: int, highs, lows, closes, c_atr: float, lookback: int = 30, min_atr_mult: float = 0.08) -> Optional[Tuple[float, float]]:
    for k in range(i - 1, max(2, i - lookback), -1):
        if lows[k - 2] > highs[k]:
            gap_size = float(lows[k - 2] - highs[k])
            if gap_size >= c_atr * min_atr_mult:
                fvg_top = float(lows[k - 2])
                fvg_bot = float(highs[k])
                if closes[i] > fvg_top and closes[k] <= fvg_top:
                    return (fvg_bot, fvg_top)
    return None


def detect_structural_breaker_short(j: int, i: int, highs, lows, opens, closes) -> bool:
    for k in range(j - 1, max(2, j - 6), -1):
        if closes[k] > opens[k]:
            return bool(closes[i] < lows[k])
    return bool(closes[i] < min(lows[j], lows[j - 1]))


def detect_structural_breaker_long(j: int, i: int, highs, lows, opens, closes) -> bool:
    for k in range(j - 1, max(2, j - 6), -1):
        if closes[k] < opens[k]:
            return bool(closes[i] > highs[k])
    return bool(closes[i] > max(highs[j], highs[j - 1]))


def is_slice_through_candle(direction: str, open_p: float, high_p: float, low_p: float, close_p: float, zone_low: float, zone_high: float) -> bool:
    candle_range = max(high_p - low_p, 1e-8)
    body = abs(close_p - open_p)
    body_ratio = body / candle_range

    if direction.upper() == "LONG":
        if close_p < open_p and body_ratio >= 0.60 and close_p < zone_low:
            lower_wick = max(close_p - low_p, 0.0) / candle_range
            if lower_wick <= 0.22:
                return True
    elif direction.upper() == "SHORT":
        if close_p > open_p and body_ratio >= 0.60 and close_p > zone_high:
            upper_wick = max(high_p - close_p, 0.0) / candle_range
            if upper_wick <= 0.22:
                return True
    return False


def calculate_ce_entry(
    direction: str,
    peak_or_valley: float,
    trigger_price: float,
    close_price: float,
    fvg_range: Optional[Tuple[float, float]] = None,
    atr: float = 0.0,
    sl_buffer_atr: float = 0.15,
    entry_model: str = "midpoint",
    orb_level: Optional[float] = None,
    tp1_r: float = 1.0,
    tp2_r: float = 3.0,
) -> Dict[str, Any]:
    direction = direction.upper()
    model = (entry_model or "midpoint").lower()

    if model == "midpoint":
        if direction == "SHORT":
            if fvg_range is not None and len(fvg_range) == 2 and fvg_range[0] > fvg_range[1]:
                fvg_top, fvg_bottom = fvg_range
                entry_price = (fvg_top + fvg_bottom) / 2.0
                zone_high = max(fvg_top, peak_or_valley)
                zone_low = min(fvg_bottom, close_price)
            elif orb_level is not None:
                entry_price = (orb_level + close_price) / 2.0
                zone_high = max(orb_level, close_price)
                zone_low = min(orb_level, close_price)
            else:
                ref_low = min(trigger_price, close_price)
                entry_price = (peak_or_valley + ref_low) / 2.0
                zone_high = peak_or_valley
                zone_low = ref_low

            zone_width = abs(zone_high - entry_price)
            dyn_buffer = (atr * sl_buffer_atr) if atr > 0 else (entry_price * 0.0015)
            dyn_buffer = max(dyn_buffer, zone_width * 0.15, (atr * 0.10) if atr > 0 else (entry_price * 0.0005))

            stop_loss = max(peak_or_valley, entry_price) + dyn_buffer
            risk_dist = max(stop_loss - entry_price, (atr * 0.15) if atr > 0 else (entry_price * 0.0005))
            tp1 = entry_price - (risk_dist * tp1_r)
            tp2 = entry_price - (risk_dist * tp2_r)
        else:
            if fvg_range is not None and len(fvg_range) == 2 and fvg_range[1] > fvg_range[0]:
                fvg_bottom, fvg_top = fvg_range
                entry_price = (fvg_top + fvg_bottom) / 2.0
                zone_high = max(fvg_top, close_price)
                zone_low = min(fvg_bottom, peak_or_valley)
            elif orb_level is not None:
                entry_price = (orb_level + close_price) / 2.0
                zone_high = max(orb_level, close_price)
                zone_low = min(orb_level, close_price)
            else:
                ref_high = max(trigger_price, close_price)
                entry_price = (peak_or_valley + ref_high) / 2.0
                zone_high = ref_high
                zone_low = peak_or_valley

            zone_width = abs(entry_price - zone_low)
            dyn_buffer = (atr * sl_buffer_atr) if atr > 0 else (entry_price * 0.0015)
            dyn_buffer = max(dyn_buffer, zone_width * 0.15, (atr * 0.10) if atr > 0 else (entry_price * 0.0005))

            stop_loss = min(peak_or_valley, entry_price) - dyn_buffer
            risk_dist = max(entry_price - stop_loss, (atr * 0.15) if atr > 0 else (entry_price * 0.0005))
            tp1 = entry_price + (risk_dist * tp1_r)
            tp2 = entry_price + (risk_dist * tp2_r)

        return {
            "entry_price": round(entry_price, 6),
            "stop_loss": round(stop_loss, 6),
            "risk_distance": round(risk_dist, 6),
            "tp1": round(tp1, 6),
            "tp2": round(tp2, 6),
            "zone_low": round(zone_low, 6),
            "zone_high": round(zone_high, 6),
            "is_limit": True,
        }
    else:
        entry_price = close_price
        if direction == "SHORT":
            stop_loss = peak_or_valley + ((atr * sl_buffer_atr) if atr > 0 else (entry_price * 0.0015))
            risk_dist = max(stop_loss - entry_price, (atr * 0.20) if atr > 0 else (entry_price * 0.0008))
            tp1 = entry_price - (risk_dist * tp1_r)
            tp2 = entry_price - (risk_dist * tp2_r)
            zone_high, zone_low = peak_or_valley, close_price
        else:
            stop_loss = peak_or_valley - ((atr * sl_buffer_atr) if atr > 0 else (entry_price * 0.0015))
            risk_dist = max(entry_price - stop_loss, (atr * 0.20) if atr > 0 else (entry_price * 0.0008))
            tp1 = entry_price + (risk_dist * tp1_r)
            tp2 = entry_price + (risk_dist * tp2_r)
            zone_high, zone_low = close_price, peak_or_valley

        return {
            "entry_price": round(entry_price, 6),
            "stop_loss": round(stop_loss, 6),
            "risk_distance": round(risk_dist, 6),
            "tp1": round(tp1, 6),
            "tp2": round(tp2, 6),
            "zone_low": round(zone_low, 6),
            "zone_high": round(zone_high, 6),
            "is_limit": False,
        }


# ============================================================================
# TARİHSEL MAKRO KAPI TABLOSU (BAĞIMSIZ KOPYA)
# ============================================================================

class StandaloneMacroGateTable:
    def __init__(self, start_date: date, end_date: date):
        self.start_date = start_date
        self.end_date = end_date
        self.daily_gates: Dict[date, Dict[str, str]] = {}
        self._build_table()

    def _build_table(self):
        t_from = (self.start_date - timedelta(days=60)).strftime("%Y-%m-%d")
        t_to = (self.end_date + timedelta(days=2)).strftime("%Y-%m-%d")
        tickers = {
            "dxy": "DX-Y.NYB",
            "us10y": "^TNX",
            "us02y": "2YY=F",
            "spx": "^GSPC",
            "vix": "^VIX",
            "gold": "GC=F",
            "brent": "BZ=F",
            "btc": "BTC-USD",
            "eth": "ETH-USD",
            "sol": "SOL-USD"
        }
        series_dict = {}
        for k, ticker in tickers.items():
            try:
                hist = yf.Ticker(ticker).history(start=t_from, end=t_to, interval="1d")
                if not hist.empty:
                    series_dict[k] = hist["Close"]
            except Exception:
                pass

        df_macro = pd.DataFrame(series_dict).ffill().bfill()
        df_macro["date"] = df_macro.index.date
        df_macro.drop_duplicates(subset=["date"], keep="last", inplace=True)
        df_macro.set_index("date", inplace=True)

        df_macro["dxy_roc_20d"] = df_macro["dxy"].pct_change(20) * 100
        df_macro["dxy_roc_5d"] = df_macro["dxy"].pct_change(5) * 100
        df_macro["delta_10y_5d"] = (df_macro["us10y"] - df_macro["us10y"].shift(5)) * 100
        df_macro["delta_02y_5d"] = (df_macro["us02y"] - df_macro["us02y"].shift(5)) * 100
        df_macro["real_yield"] = df_macro["us10y"] - 2.30
        df_macro["spx_sma50"] = df_macro["spx"].rolling(50).mean()
        df_macro["gold_roc_5d"] = df_macro["gold"].pct_change(5) * 100
        df_macro["btc_roc_20d"] = df_macro["btc"].pct_change(20) * 100
        df_macro["btc_roc_5d"] = df_macro["btc"].pct_change(5) * 100

        all_dates = sorted(df_macro.index)
        for idx, d in enumerate(all_dates):
            if idx == 0:
                continue
            row = df_macro.loc[all_dates[idx - 1]]
            dxy_20d = float(row.get("dxy_roc_20d", 0.0) or 0.0)
            dxy_5d = float(row.get("dxy_roc_5d", 0.0) or 0.0)
            d10y_5d = float(row.get("delta_10y_5d", 0.0) or 0.0)
            d02y_5d = float(row.get("delta_02y_5d", 0.0) or 0.0)
            vix_val = float(row.get("vix", 18.0) or 18.0)
            spx_val = float(row.get("spx", 5000.0) or 5000.0)
            spx_sma = float(row.get("spx_sma50", spx_val) or spx_val)
            real_y = float(row.get("real_yield", 1.5) or 1.5)
            gold_5d = float(row.get("gold_roc_5d", 0.0) or 0.0)
            btc_20d = float(row.get("btc_roc_20d", 0.0) or 0.0)
            btc_5d = float(row.get("btc_roc_5d", 0.0) or 0.0)

            usd_bull = (dxy_20d > 0.35) or (dxy_5d > 0.25 and d10y_5d > -2.0)
            usd_bear = (dxy_20d < -0.35) or (dxy_5d < -0.25 and d10y_5d < 2.0)

            gates = {}
            for sym in ["EURUSD", "GBPUSD", "NZDUSD"]:
                if usd_bull:
                    gates[sym] = "SHORT_ONLY"
                elif usd_bear:
                    gates[sym] = "LONG_ONLY"
                else:
                    gates[sym] = "NEUTRAL_RANGE"

            for sym in ["USDJPY", "USDCAD", "USDCHF"]:
                if usd_bull:
                    gates[sym] = "LONG_ONLY"
                elif usd_bear:
                    gates[sym] = "SHORT_ONLY"
                else:
                    gates[sym] = "NEUTRAL_RANGE"

            gates["EURGBP"] = "SHORT_ONLY" if dxy_5d > 0.1 else ("LONG_ONLY" if dxy_5d < -0.1 else "NEUTRAL_RANGE")

            if gold_5d > 0.8 or (real_y < 1.85 and usd_bear):
                gates["XAUUSD"] = "LONG_ONLY"
            elif gold_5d < -1.2 and usd_bull and d10y_5d > 4.0:
                gates["XAUUSD"] = "SHORT_ONLY"
            else:
                gates["XAUUSD"] = "NEUTRAL_RANGE"

            eq_bull = (spx_val > spx_sma and vix_val < 22.0)
            eq_bear = (spx_val < spx_sma and vix_val > 20.0) or (vix_val > 25.0)
            gates["NAS100"] = "LONG_ONLY" if eq_bull else ("SHORT_ONLY" if eq_bear else "NEUTRAL_RANGE")

            if vix_val > 26.0 and btc_5d < -3.0:
                gates["BTCUSD"] = "SHORT_ONLY"
            elif btc_20d > 2.0 or (btc_5d > 1.0 and vix_val < 23.0):
                gates["BTCUSD"] = "LONG_ONLY"
            elif btc_20d < -5.0 and btc_5d < -2.0:
                gates["BTCUSD"] = "SHORT_ONLY"
            else:
                gates["BTCUSD"] = "NEUTRAL_RANGE"

            self.daily_gates[d] = gates

    def get_gate(self, dt: datetime, symbol: str) -> str:
        d = dt.date()
        if d in self.daily_gates:
            return self.daily_gates[d].get(symbol, "NEUTRAL_RANGE")
        past = [k for k in self.daily_gates.keys() if k <= d]
        if past:
            return self.daily_gates[max(past)].get(symbol, "NEUTRAL_RANGE")
        return "NEUTRAL_RANGE"


# ============================================================================
# VERİ YÜKLEYİCİ (M15 VE M1 DESTEKLİ)
# ============================================================================

def fetch_mt5_data(symbol: str, utc_from: datetime, utc_to: datetime, timeframe=mt5.TIMEFRAME_M15) -> Optional[pd.DataFrame]:
    if not mt5.initialize():
        return None

    warmup_days = 25 if timeframe == mt5.TIMEFRAME_M15 else 5
    warmup_from = utc_from - timedelta(days=warmup_days)
    rates = mt5.copy_rates_range(symbol, timeframe, warmup_from, utc_to)
    if rates is None or len(rates) < 300:
        return None

    df = pd.DataFrame(rates)
    df["datetime"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.rename(columns={"tick_volume": "volume"}, inplace=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    df["date"] = df["datetime"].dt.date
    df["weekday"] = df["datetime"].dt.weekday
    df["hour"] = df["datetime"].dt.hour
    df["minute"] = df["datetime"].dt.minute

    daily = df.groupby("date").agg({"high": "max", "low": "min"}).shift(1)
    df["pdh"] = df["date"].map(daily["high"]).fillna(0.0)
    df["pdl"] = df["date"].map(daily["low"]).fillna(0.0)

    asian_bars = df[df["hour"] < 6]
    ash_map = asian_bars.groupby("date")["high"].max()
    asl_map = asian_bars.groupby("date")["low"].min()
    df["ash"] = df["date"].map(ash_map).fillna(0.0)
    df["asl"] = df["date"].map(asl_map).fillna(0.0)

    if timeframe == mt5.TIMEFRAME_M15:
        df["htf_ema50"] = df["close"].ewm(span=200, adjust=False).mean()
        df["htf_ema200"] = df["close"].ewm(span=800, adjust=False).mean()
    else:
        # M1 için 1H/4H eşdeğeri EMA
        df["htf_ema50"] = df["close"].ewm(span=600, adjust=False).mean()
        df["htf_ema200"] = df["close"].ewm(span=2400, adjust=False).mean()

    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            np.abs(df["high"] - df["close"].shift(1)),
            np.abs(df["low"] - df["close"].shift(1))
        )
    )
    df["atr"] = tr.rolling(14).mean().fillna(df["high"] - df["low"])
    df["vol_sma"] = df["volume"].rolling(20).mean().fillna(df["volume"])
    return df


# ============================================================================
# SİMÜLASYON MOTORU
# ============================================================================

def run_sfp_ifvg_simulation(
    data_map: Dict[str, pd.DataFrame],
    utc_from: datetime,
    macro_table: Optional[StandaloneMacroGateTable] = None,
    use_macro_gate: bool = True,
    include_orb: bool = False,
    exit_mode: str = "STANDARD_TP2_3R",  # "STANDARD_TP2_3R" veya "CONVEXITY_RUNNER"
) -> Dict[str, Any]:
    all_trades = []
    symbol_stats = {}
    start_ts = pd.Timestamp(utc_from.replace(tzinfo=None))

    for symbol, df in data_map.items():
        sl_buffer_atr = get_sl_buffer(symbol)
        crypto_sym = is_crypto(symbol)
        allow_orb_sym = include_orb and (symbol in FOREX_SYMBOLS)

        n = len(df)
        opens, highs, lows, closes = df["open"].values, df["high"].values, df["low"].values, df["close"].values
        volumes, vol_smas = df["volume"].values, df["vol_sma"].values
        pdhs, pdls = df["pdh"].values, df["pdl"].values
        ashs, asls = df["ash"].values, df["asl"].values
        atrs = df["atr"].values
        ema50s, ema200s = df["htf_ema50"].values, df["htf_ema200"].values
        weekdays, hours, minutes = df["weekday"].values, df["hour"].values, df["minute"].values
        dts = df["datetime"].dt.strftime("%Y-%m-%d %H:%M").values
        dates_raw = df["datetime"].values

        orb_high = np.zeros(n, dtype=float)
        orb_low = np.zeros(n, dtype=float)
        in_orb_window = np.zeros(n, dtype=bool)
        if allow_orb_sym:
            curr_oh, curr_ol, in_sess, sess_exp = 0.0, 0.0, False, 0
            for idx_b in range(n):
                if weekdays[idx_b] in [5, 6]:
                    continue
                if hours[idx_b] == 7 and minutes[idx_b] == 0:
                    curr_oh, curr_ol, in_sess, sess_exp = highs[idx_b], lows[idx_b], True, idx_b + 15
                elif in_sess and idx_b <= sess_exp:
                    orb_high[idx_b], orb_low[idx_b], in_orb_window[idx_b] = curr_oh, curr_ol, True
                elif idx_b > sess_exp:
                    in_sess = False

        sym_trades = []
        active_position = None
        cooldown_until = 0

        for i in range(100, n):
            c_open, c_high, c_low, c_close = opens[i], highs[i], lows[i], closes[i]
            c_atr = atrs[i]
            c_vol, c_vol_sma = volumes[i], vol_smas[i]
            wday, h_val = weekdays[i], hours[i]
            bar_rng = max(c_high - c_low, 1e-8)

            if active_position is not None:
                p = active_position
                entry = p["entry"]
                sl = p["current_sl"]
                tp1 = p["tp1"]
                tp2 = p["tp2"]
                status = p["status"]
                p_dir = p["direction"]

                if status == "PENDING_LIMIT":
                    p["bars_waiting"] += 1
                    if p["bars_waiting"] >= p["max_wait_bars"]:
                        active_position = None
                        continue

                    is_long = (p_dir == "LONG")
                    stop_hit_before = (c_low <= sl) if is_long else (c_high >= sl)
                    reached_entry = (c_low <= entry and c_open >= sl) if is_long else (c_high >= entry and c_open <= sl)

                    if stop_hit_before and not reached_entry:
                        active_position = None
                        continue

                    if reached_entry:
                        if is_slice_through_candle(p_dir, c_open, c_high, c_low, c_close, p["zone_low"], p["zone_high"]):
                            active_position = None
                            continue
                        p["status"] = "OPEN"
                        status = "OPEN"
                    else:
                        continue

                p["bars_held"] += 1
                closed = False
                close_reason = ""
                r_res = 0.0
                risk_dist = max(abs(entry - p["original_sl"]), 1e-8)

                if exit_mode == "STANDARD_TP2_3R":
                    if p_dir == "LONG":
                        if c_high >= tp1 and status == "OPEN":
                            p["status"] = "TP1_HIT"
                            p["current_sl"] = entry
                            sl = entry
                        if c_high >= tp2:
                            closed, close_reason, r_res = True, "TP2_HIT", 2.0
                        elif c_low <= sl:
                            closed = True
                            close_reason = "BREAK_EVEN" if status == "TP1_HIT" else "STOP_LOSS"
                            r_res = 0.5 if status == "TP1_HIT" else -1.0
                        elif p["bars_held"] >= 192:
                            closed, close_reason = True, "TIME_EXIT"
                            r_res = (c_close - entry) / risk_dist
                    else:
                        if c_low <= tp1 and status == "OPEN":
                            p["status"] = "TP1_HIT"
                            p["current_sl"] = entry
                            sl = entry
                        if c_low <= tp2:
                            closed, close_reason, r_res = True, "TP2_HIT", 2.0
                        elif c_high >= sl:
                            closed = True
                            close_reason = "BREAK_EVEN" if status == "TP1_HIT" else "STOP_LOSS"
                            r_res = 0.5 if status == "TP1_HIT" else -1.0
                        elif p["bars_held"] >= 192:
                            closed, close_reason = True, "TIME_EXIT"
                            r_res = (entry - c_close) / risk_dist

                elif exit_mode == "CONVEXITY_RUNNER":
                    # Bilet A: +2.0R'da %50 Kâr Al + BE | Bilet B: 2.5x ATR Trailing Stop Runner
                    tp_2r = entry + (2.0 * risk_dist) if p_dir == "LONG" else entry - (2.0 * risk_dist)
                    if p_dir == "LONG":
                        if c_high >= tp_2r and status == "OPEN":
                            p["status"] = "TICKET_A_LOCKED"
                            p["current_sl"] = max(entry, c_high - 2.5 * c_atr)
                            sl = p["current_sl"]
                        elif status == "TICKET_A_LOCKED":
                            new_trail = c_high - 2.5 * c_atr
                            if new_trail > sl:
                                p["current_sl"] = new_trail
                                sl = new_trail

                        if c_low <= sl:
                            closed = True
                            if status == "TICKET_A_LOCKED":
                                runner_r = (sl - entry) / risk_dist
                                r_res = (2.0 * 0.5) + (runner_r * 0.5)
                                close_reason = f"RUNNER_TRAIL ({r_res:+.2f}R)"
                            else:
                                close_reason = "STOP_LOSS"
                                r_res = -1.0
                        elif p["bars_held"] >= 192:
                            closed = True
                            runner_r = (c_close - entry) / risk_dist
                            r_res = (2.0 * 0.5 + runner_r * 0.5) if status == "TICKET_A_LOCKED" else runner_r
                            close_reason = "TIME_EXIT"
                    else:
                        if c_low <= tp_2r and status == "OPEN":
                            p["status"] = "TICKET_A_LOCKED"
                            p["current_sl"] = min(entry, c_low + 2.5 * c_atr)
                            sl = p["current_sl"]
                        elif status == "TICKET_A_LOCKED":
                            new_trail = c_low + 2.5 * c_atr
                            if new_trail < sl:
                                p["current_sl"] = new_trail
                                sl = new_trail

                        if c_high >= sl:
                            closed = True
                            if status == "TICKET_A_LOCKED":
                                runner_r = (entry - sl) / risk_dist
                                r_res = (2.0 * 0.5) + (runner_r * 0.5)
                                close_reason = f"RUNNER_TRAIL ({r_res:+.2f}R)"
                            else:
                                close_reason = "STOP_LOSS"
                                r_res = -1.0
                        elif p["bars_held"] >= 192:
                            closed = True
                            runner_r = (entry - c_close) / risk_dist
                            r_res = (2.0 * 0.5 + runner_r * 0.5) if status == "TICKET_A_LOCKED" else runner_r
                            close_reason = "TIME_EXIT"

                if closed:
                    p["closed_at"] = dts[i]
                    p["exit_price"] = c_close
                    p["close_reason"] = close_reason
                    p["r_result"] = round(float(r_res), 2)
                    sym_trades.append(p)
                    all_trades.append(p)
                    active_position = None
                    cooldown_until = i + 3
                continue

            bar_raw = pd.Timestamp(dates_raw[i]).tz_localize(None)
            if bar_raw < start_ts or i < cooldown_until:
                continue

            is_weekend = wday in [5, 6]
            if is_weekend:
                if not crypto_sym:
                    continue
                sfp_session_active = (7 <= h_val <= 11) or (13 <= h_val <= 17)
                orb_session_active = False
            else:
                sfp_session_active = (7 <= h_val <= 11) or (13 <= h_val <= 17)
                orb_session_active = allow_orb_sym and in_orb_window[i] and (7 <= h_val <= 11)

            if pdhs[i] == 0.0 or pdls[i] == 0.0:
                continue

            # 4H Pro-Trend AAA Filtresi
            htf_bull = (c_close > ema50s[i] > ema200s[i])
            htf_bear = (c_close < ema50s[i] < ema200s[i])
            allow_short = htf_bear
            allow_long = htf_bull

            if use_macro_gate and macro_table is not None:
                cur_dt = pd.to_datetime(dates_raw[i]).to_pydatetime()
                gate_val = macro_table.get_gate(cur_dt, symbol)
                if gate_val in ["NEUTRAL_RANGE", "NO_TRADE", "DEFENSIVE_HOLD", "VETO"]:
                    allow_short = False
                    allow_long = False
                elif gate_val == "SHORT_ONLY":
                    allow_long = False
                elif gate_val == "LONG_ONLY":
                    allow_short = False

            sfp_sig = None
            orb_sig = None

            if sfp_session_active and (allow_short or allow_long):
                if allow_short:
                    for j in [i - 1, i - 2, i - 3]:
                        if j < 25:
                            continue
                        j_rng = max(highs[j] - lows[j], 1e-8)
                        j_u_wick = (highs[j] - max(opens[j], closes[j])) / j_rng
                        if j_u_wick < 0.45:
                            continue

                        sw_hi, p_name = False, ""
                        if highs[j] > pdhs[j] and closes[j] < pdhs[j]:
                            sw_hi, p_name = True, "PDH"
                        elif (7 <= hours[j] <= 11) and ashs[j] > 0.0 and highs[j] > ashs[j] and closes[j] < ashs[j]:
                            sw_hi, p_name = True, "ASIAN_HIGH"

                        if not sw_hi:
                            continue

                        peak = highs[j]
                        if any(highs[m] > peak for m in range(j + 1, i + 1)):
                            continue

                        mss_trig = min(lows[j], lows[j - 1])
                        if any(closes[m] < mss_trig for m in range(j + 1, i)):
                            continue

                        if closes[i] < mss_trig and (abs(closes[i] - opens[i]) / bar_rng) >= 0.35:
                            if not detect_structural_breaker_short(j, i, highs, lows, opens, closes):
                                continue
                            target_zone = detect_ifvg_short(i, highs, lows, closes, c_atr)
                            if target_zone is not None:
                                ce_calc = calculate_ce_entry(
                                    direction="SHORT",
                                    peak_or_valley=peak,
                                    trigger_price=mss_trig,
                                    close_price=closes[i],
                                    fvg_range=target_zone,
                                    atr=c_atr,
                                    sl_buffer_atr=sl_buffer_atr,
                                    entry_model="midpoint",
                                    tp1_r=1.0,
                                    tp2_r=3.0,
                                )
                                sfp_sig = {
                                    "strategy": "SFP_IFVG_BREAKER",
                                    "direction": "SHORT",
                                    "grade": "AAA",
                                    "entry": ce_calc["entry_price"],
                                    "sl": ce_calc["stop_loss"],
                                    "tp1": ce_calc["tp1"],
                                    "tp2": ce_calc["tp2"],
                                    "risk": ce_calc["risk_distance"],
                                    "zone_low": ce_calc["zone_low"],
                                    "zone_high": ce_calc["zone_high"],
                                    "is_limit": ce_calc["is_limit"],
                                    "pool": p_name,
                                    "trigger_time": dts[i],
                                }
                                break

                if allow_long and sfp_sig is None:
                    for j in [i - 1, i - 2, i - 3]:
                        if j < 25:
                            continue
                        j_rng = max(highs[j] - lows[j], 1e-8)
                        j_l_wick = (min(opens[j], closes[j]) - lows[j]) / j_rng
                        if j_l_wick < 0.45:
                            continue

                        sw_lo, p_name = False, ""
                        if lows[j] < pdls[j] and closes[j] > pdls[j]:
                            sw_lo, p_name = True, "PDL"
                        elif (7 <= hours[j] <= 11) and asls[j] > 0.0 and lows[j] < asls[j] and closes[j] > asls[j]:
                            sw_lo, p_name = True, "ASIAN_LOW"

                        if not sw_lo:
                            continue

                        valley = lows[j]
                        if any(lows[m] < valley for m in range(j + 1, i + 1)):
                            continue

                        mss_trig = max(highs[j], highs[j - 1])
                        if any(closes[m] > mss_trig for m in range(j + 1, i)):
                            continue

                        if closes[i] > mss_trig and (abs(closes[i] - opens[i]) / bar_rng) >= 0.35:
                            if not detect_structural_breaker_long(j, i, highs, lows, opens, closes):
                                continue
                            target_zone = detect_ifvg_long(i, highs, lows, closes, c_atr)
                            if target_zone is not None:
                                ce_calc = calculate_ce_entry(
                                    direction="LONG",
                                    peak_or_valley=valley,
                                    trigger_price=mss_trig,
                                    close_price=closes[i],
                                    fvg_range=target_zone,
                                    atr=c_atr,
                                    sl_buffer_atr=sl_buffer_atr,
                                    entry_model="midpoint",
                                    tp1_r=1.0,
                                    tp2_r=3.0,
                                )
                                sfp_sig = {
                                    "strategy": "SFP_IFVG_BREAKER",
                                    "direction": "LONG",
                                    "grade": "AAA",
                                    "entry": ce_calc["entry_price"],
                                    "sl": ce_calc["stop_loss"],
                                    "tp1": ce_calc["tp1"],
                                    "tp2": ce_calc["tp2"],
                                    "risk": ce_calc["risk_distance"],
                                    "zone_low": ce_calc["zone_low"],
                                    "zone_high": ce_calc["zone_high"],
                                    "is_limit": ce_calc["is_limit"],
                                    "pool": p_name,
                                    "trigger_time": dts[i],
                                }
                                break

            if orb_session_active and sfp_sig is None:
                o_h, o_l = orb_high[i], orb_low[i]
                vol_ratio = c_vol / max(c_vol_sma, 1e-8)
                if allow_long and c_close > o_h and closes[i - 1] <= o_h and vol_ratio >= 2.0:
                    if (c_high - c_close) / bar_rng <= 0.35 and (c_close - c_open) / bar_rng >= 0.40:
                        ce_calc = calculate_ce_entry("LONG", o_l, o_h, c_close, orb_level=o_h, atr=c_atr, sl_buffer_atr=sl_buffer_atr)
                        orb_sig = {
                            "strategy": "ORB_SESSION_BREAKOUT",
                            "direction": "LONG",
                            "grade": "A+",
                            "entry": ce_calc["entry_price"],
                            "sl": ce_calc["stop_loss"],
                            "tp1": ce_calc["tp1"],
                            "tp2": ce_calc["tp2"],
                            "risk": ce_calc["risk_distance"],
                            "zone_low": ce_calc["zone_low"],
                            "zone_high": ce_calc["zone_high"],
                            "is_limit": ce_calc["is_limit"],
                            "pool": "LONDON_ORB",
                            "trigger_time": dts[i],
                        }
                elif allow_short and c_close < o_l and closes[i - 1] >= o_l and vol_ratio >= 2.0:
                    if (c_close - c_low) / bar_rng <= 0.35 and (c_open - c_close) / bar_rng >= 0.40:
                        ce_calc = calculate_ce_entry("SHORT", o_h, o_l, c_close, orb_level=o_l, atr=c_atr, sl_buffer_atr=sl_buffer_atr)
                        orb_sig = {
                            "strategy": "ORB_SESSION_BREAKOUT",
                            "direction": "SHORT",
                            "grade": "A+",
                            "entry": ce_calc["entry_price"],
                            "sl": ce_calc["stop_loss"],
                            "tp1": ce_calc["tp1"],
                            "tp2": ce_calc["tp2"],
                            "risk": ce_calc["risk_distance"],
                            "zone_low": ce_calc["zone_low"],
                            "zone_high": ce_calc["zone_high"],
                            "is_limit": ce_calc["is_limit"],
                            "pool": "LONDON_ORB",
                            "trigger_time": dts[i],
                        }

            sig = sfp_sig or orb_sig
            if sig is not None:
                active_position = {
                    "symbol": symbol,
                    "strategy": sig["strategy"],
                    "direction": sig["direction"],
                    "grade": sig["grade"],
                    "opened_at": sig["trigger_time"],
                    "entry": sig["entry"],
                    "original_sl": sig["sl"],
                    "current_sl": sig["sl"],
                    "tp1": sig["tp1"],
                    "tp2": sig["tp2"],
                    "zone_low": sig["zone_low"],
                    "zone_high": sig["zone_high"],
                    "status": "PENDING_LIMIT" if sig["is_limit"] else "OPEN",
                    "bars_held": 0,
                    "bars_waiting": 0,
                    "max_wait_bars": 32,
                    "pool": sig["pool"],
                }

        if sym_trades:
            wins = sum(1 for t in sym_trades if t["r_result"] > 0)
            symbol_stats[symbol] = {
                "trades": len(sym_trades),
                "wins": wins,
                "win_rate": round(wins / len(sym_trades) * 100, 1),
                "net_r": round(sum(t["r_result"] for t in sym_trades), 2),
            }

    all_trades.sort(key=lambda x: x["opened_at"])
    total_trades = len(all_trades)
    wins = sum(1 for t in all_trades if t["r_result"] > 0)
    losses = sum(1 for t in all_trades if t["r_result"] < 0)
    net_r = round(sum(t["r_result"] for t in all_trades), 2)
    win_rate = round(wins / total_trades * 100, 1) if total_trades > 0 else 0.0

    bal_fixed = 10000.0 + (net_r * 200.0)
    bal_dyn = 10000.0
    peak_dyn = 10000.0
    max_dd = 0.0
    for t in all_trades:
        bal_dyn += t["r_result"] * (bal_dyn * 0.02)
        if bal_dyn > peak_dyn:
            peak_dyn = bal_dyn
        dd = (peak_dyn - bal_dyn) / peak_dyn * 100.0
        if dd > max_dd:
            max_dd = dd

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate,
        "net_r": net_r,
        "ending_balance_fixed": round(bal_fixed, 2),
        "net_profit_fixed": round(bal_fixed - 10000.0, 2),
        "ending_balance_compounding": round(bal_dyn, 2),
        "net_profit_compounding": round(bal_dyn - 10000.0, 2),
        "max_dd_pct": round(max_dd, 2),
        "symbol_stats": symbol_stats,
        "trades": all_trades,
    }


def main():
    utc_from_1mo = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
    utc_to = datetime(2026, 10, 11, 23, 59, tzinfo=timezone.utc)

    print("\n" + "═" * 95)
    print(" 🤖 MATEMATİKSEL BOT (BEGONYA CONVEXITY ENGINE) │ SFP + iFVG + BREAKER DOĞRULAMA TESTİ 🤖 ")
    print("═" * 95)

    print("📡 1/3: Makro Kapı Tablosu ve MT5 M15 Verileri Yükleniyor (Son 1 Ay: 2026-09-11 -> 2026-10-11)...")
    macro_table = StandaloneMacroGateTable(utc_from_1mo.date(), utc_to.date())

    m15_data_map = {}
    for sym in TIER1_SYMBOLS:
        df = fetch_mt5_data(sym, utc_from_1mo, utc_to, timeframe=mt5.TIMEFRAME_M15)
        if df is not None:
            m15_data_map[sym] = df

    # 1. SENARYO A: Sadece SFP + iFVG + Breaker (Begonya Makro Kapılı - ORB Hariç)
    res_sfp_only_macro = run_sfp_ifvg_simulation(
        m15_data_map, utc_from_1mo, macro_table=macro_table, use_macro_gate=True, include_orb=False, exit_mode="STANDARD_TP2_3R"
    )

    # 2. SENARYO B: SFP + iFVG + Breaker + London ORB (custom_1m_backtest.py Tam Portföy)
    res_sfp_orb_macro = run_sfp_ifvg_simulation(
        m15_data_map, utc_from_1mo, macro_table=macro_table, use_macro_gate=True, include_orb=True, exit_mode="STANDARD_TP2_3R"
    )

    # 3. SENARYO C: Matematiksel Bot (Saf Matematiksel - Makro Kapısız, Sadece 4H EMA50/200 AAA + SFP+iFVG+Breaker)
    res_sfp_pure_math = run_sfp_ifvg_simulation(
        m15_data_map, utc_from_1mo, macro_table=None, use_macro_gate=False, include_orb=False, exit_mode="STANDARD_TP2_3R"
    )

    # 4. SENARYO D: SFP + iFVG + Breaker + Convexity Runner Çıkışı (Bilet A +2R / Bilet B 2.5x ATR Trailing)
    res_sfp_convexity = run_sfp_ifvg_simulation(
        m15_data_map, utc_from_1mo, macro_table=macro_table, use_macro_gate=True, include_orb=False, exit_mode="CONVEXITY_RUNNER"
    )

    # 5. SENARYO E: Gerçek 1 Dakikalık (M1 Timeframe) Mumlar Üzerinde Son 1 Ay Testi
    print("📡 2/3: Gerçek 1 Dakikalık (M1 Timeframe) Mum Verileri Yükleniyor ve Test Ediliyor...")
    m1_data_map = {}
    for sym in TIER1_SYMBOLS:
        df_m1 = fetch_mt5_data(sym, utc_from_1mo, utc_to, timeframe=mt5.TIMEFRAME_M1)
        if df_m1 is not None:
            m1_data_map[sym] = df_m1

    res_m1_timeframe = run_sfp_ifvg_simulation(
        m1_data_map, utc_from_1mo, macro_table=macro_table, use_macro_gate=True, include_orb=False, exit_mode="STANDARD_TP2_3R"
    )

    output = {
        "1_SFP_IFVG_BREAKER_1MO_M15_MACRO_ON": res_sfp_only_macro,
        "2_CUSTOM_1M_FULL_WITH_ORB_1MO_M15": res_sfp_orb_macro,
        "3_PURE_MATHEMATICAL_NO_MACRO_1MO_M15": res_sfp_pure_math,
        "4_SFP_IFVG_WITH_CONVEXITY_RUNNER_EXIT": res_sfp_convexity,
        "5_ACTUAL_1_MINUTE_TIMEFRAME_M1_TEST": res_m1_timeframe,
    }

    out_path = Path(__file__).resolve().parent.parent / "logs" / "sfp_ifvg_1m_test_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "═" * 95)
    print(" 📊 TEST SONUÇLARI ÖZET TABLOSU ($10,000 Başlangıç │ %2.0 Risk = $200 / 1R) 📊 ")
    print("═" * 95)
    for label, r in [
        ("1) SFP+iFVG+Breaker (1 Aylık M15 - Makro Kapılı)", res_sfp_only_macro),
        ("2) SFP+iFVG+Breaker + ORB (custom_1m_backtest Birebir)", res_sfp_orb_macro),
        ("3) Saf Matematiksel SFP+iFVG+Breaker (Makro Kapısız)", res_sfp_pure_math),
        ("4) SFP+iFVG+Breaker + Convexity Runner Çıkışı", res_sfp_convexity),
        ("5) Gerçek 1 Dakikalık (M1 Mumları) SFP+iFVG+Breaker", res_m1_timeframe),
    ]:
        print(
            f"• {label:<52} │ İşlem: {r['total_trades']:>2} ({r['wins']}W/{r['losses']}L) "
            f"│ WR: %{r['win_rate_pct']:>5.1f} │ Net: {r['net_r']:>+6.2f}R "
            f"│ Sabit Kâr: ${r['net_profit_fixed']:>+8.2f} │ MaxDD: %{r['max_dd_pct']:.2f}"
        )
    print("═" * 95)

    print("\n📋 [DETAY 1] SFP+iFVG+Breaker (1 Aylık M15 - Makro Kapılı) İşlem Dökümü:")
    for idx, t in enumerate(res_sfp_only_macro["trades"], 1):
        print(
            f"  #{idx} │ {t['opened_at']} -> {t['closed_at']} │ {t['symbol']:<6} {t['direction']:<5} "
            f"│ Havuz: {t['pool']:<10} │ Giriş: {t['entry']:<10.5f} │ Çıkış: {t['close_reason']:<12} │ Sonuç: {t['r_result']:+.2f}R (${t['r_result']*200:+.2f})"
        )

    print("\n📋 [DETAY 2] Saf Matematiksel (Makro Kapısız, Sadece 4H EMA50/200 AAA) İşlem Dökümü:")
    for idx, t in enumerate(res_sfp_pure_math["trades"], 1):
        print(
            f"  #{idx} │ {t['opened_at']} -> {t['closed_at']} │ {t['symbol']:<6} {t['direction']:<5} "
            f"│ Havuz: {t['pool']:<10} │ Giriş: {t['entry']:<10.5f} │ Çıkış: {t['close_reason']:<12} │ Sonuç: {t['r_result']:+.2f}R (${t['r_result']*200:+.2f})"
        )


if __name__ == "__main__":
    main()
