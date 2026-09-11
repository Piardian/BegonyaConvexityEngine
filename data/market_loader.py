import os
import json
import logging
from pathlib import Path
from typing import Dict, Optional
import pandas as pd
import numpy as np
import yfinance as yf

logger = logging.getLogger("MarketLoader")

TICKER_MAP = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "CAD=X",
    "USDCHF": "CHF=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    "XAUUSD": "GC=F",       # Altın Vadeli
    "CL_OIL": "CL=F",       # Ham Petrol Vadeli
    "SILVER": "SI=F",       # Gümüş Vadeli
    "NAS100": "NQ=F",       # Nasdaq 100 Vadeli
    "SP500": "ES=F",        # S&P 500 Vadeli
    "BTCUSD": "BTC-USD",    # Bitcoin
    "ETHUSD": "ETH-USD",    # Ethereum
    "SOLUSD": "SOL-USD"     # Solana
}

def get_pip_factor(symbol: str) -> float:
    s = symbol.upper()
    if "JPY" in s:
        return 100.0
    if "XAU" in s or "GOLD" in s or "SILVER" in s:
        return 10.0
    if "CL_OIL" in s:
        return 100.0  # 1 sent = 1 pip
    if any(c in s for c in ["BTC", "ETH", "NAS", "NQ", "SP500", "ES"]):
        return 1.0
    if "SOL" in s:
        return 10.0
    return 10000.0

class MarketLoader:
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or (Path(__file__).resolve().parent.parent / "cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load_symbol_data(self, symbol: str, period: str = "5y", force_refresh: bool = False) -> pd.DataFrame:
        """
        yfinance üzerinden günlük (D1) barları çeker.
        Akıllı Önbellek: Eğer cache dosyası bayatsa (son bar 2+ günden eskiyse) veya force_refresh=True ise tazeler.
        Lookahead bias'ı önlemek için Donchian tepe/dipleri 1 bar kaydırılır (shift(1)).
        """
        ticker = TICKER_MAP.get(symbol.upper(), symbol)
        cache_file = self.cache_dir / f"{symbol.upper()}_1d.csv"

        df = pd.DataFrame()
        need_download = force_refresh

        if cache_file.exists() and not force_refresh:
            try:
                df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                if df.empty or len(df) < 500:
                    need_download = True
                else:
                    # Tarih tazelik kontrolü: Son bar bugünden 2+ gün eskiyse güncelle
                    last_date = df.index[-1]
                    now_utc = pd.Timestamp.now(tz="UTC")
                    if last_date.tz is None:
                        last_date = last_date.tz_localize("UTC")
                    if (now_utc - last_date).days >= 2:
                        need_download = True
            except Exception:
                need_download = True
                df = pd.DataFrame()
        else:
            need_download = True

        if need_download:
            try:
                logger.info(f"📡 [{symbol}] Güncel D1 verisi çekiliyor ({ticker})...")
                df = yf.download(ticker, period=period, interval="1d", progress=False)
                if df.empty:
                    logger.warning(f"⚠️ [{symbol}] Veri boş döndü.")
                    return pd.DataFrame()

                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0].lower() for c in df.columns]
                else:
                    df.columns = [c.lower() for c in df.columns]

                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                else:
                    df.index = df.index.tz_convert("UTC")

                df.sort_index(inplace=True)
                for col in ["open", "high", "low", "close"]:
                    df[col] = df[col].astype(float)

                df.to_csv(cache_file)
            except Exception as e:
                logger.error(f"Veri çekme hatası ({symbol}): {e}")
                return pd.DataFrame()

        # Göstergeleri hesapla
        # 1. ATR(14)
        high = df["high"]
        low = df["low"]
        close_prev = df["close"].shift(1)
        tr = pd.concat([
            high - low,
            (high - close_prev).abs(),
            (low - close_prev).abs()
        ], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(window=14).mean()

        # 2. Donchian 20 Günlük Kanalı (Lookahead-Free: shift(1) ile dünün zirve ve dibi)
        df["donchian_high_20"] = df["high"].rolling(window=20).max().shift(1)
        df["donchian_low_20"] = df["low"].rolling(window=20).min().shift(1)

        # 3. 20 Günlük EMA (Kârı sürme / Trailing için)
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

        # 4. 200 Günlük EMA (Büyük rejim filtresi)
        df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

        df.dropna(subset=["atr14", "donchian_high_20", "donchian_low_20"], inplace=True)
        return df
