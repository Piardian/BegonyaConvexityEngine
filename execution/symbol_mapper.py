import logging
from typing import Dict, Optional, List
import MetaTrader5 as mt5

logger = logging.getLogger("SymbolMapper")

class SymbolMapper:
    """
    Broker bazlı sembol eşleme ve otomatik son ek/ön ek (suffix/prefix) çözümleyici.
    Örnek:
      XAUUSD -> XAUUSD.pro, XAUUSD_i, GOLD
      NAS100 -> USTECH100M, US100, NAS100, NDX
      SP500  -> US500, US500M, SPX500
      BTCUSD -> BTC, BTCUSD, BTCUSD.c
    """

    # Varsayılan arama havuzları
    CANDIDATE_MAP = {
        "EURUSD": ["EURUSD", "EURUSD.pro", "EURUSD_i", "EURUSD.raw", "EURUSDm"],
        "GBPUSD": ["GBPUSD", "GBPUSD.pro", "GBPUSD_i", "GBPUSD.raw", "GBPUSDm"],
        "USDJPY": ["USDJPY", "USDJPY.pro", "USDJPY_i", "USDJPY.raw", "USDJPYm"],
        "USDCHF": ["USDCHF", "USDCHF.pro", "USDCHF_i", "USDCHF.raw", "USDCHFm"],
        "XAUUSD": ["XAUUSD", "GOLD", "XAUUSD.pro", "XAUUSD_i", "XAUUSD.raw", "XAUUSDm"],
        "SILVER": ["XAGUSD", "SILVER", "XAGUSD.pro", "XAGUSD_i"],
        "CL_OIL": ["USOIL", "XTIUSD", "WTICRUDE", "CRUDE_OIL"],
        "NAS100": ["USTECH100M", "NAS100", "US100", "NDX", "TECH", "USTECH"],
        "SP500":  ["US500", "SP500", "US500M", "SPX500", "SPX"],
        "BTCUSD": ["BTCUSD", "BTC", "BTCUSD.c", "XBTUSD", "BTCUSDT"],
        "ETHUSD": ["ETHUSD", "ETH", "ETHUSD.c", "ETHUSDT"],
        "SOLUSD": ["SOLUSD", "SOL", "SOLUSD.c", "SOLUSDT"],
    }

    def __init__(self, custom_overrides: Optional[Dict[str, str]] = None):
        self.overrides = custom_overrides or {}
        self.resolved_cache: Dict[str, str] = {}

    def resolve(self, generic_symbol: str) -> Optional[str]:
        """Broker MT5 terminalinde işlem gören gerçek sembol adını çözer."""
        if generic_symbol in self.resolved_cache:
            return self.resolved_cache[generic_symbol]

        # 1. Kullanıcı manuel override kontrolü
        if generic_symbol in self.overrides:
            ov = self.overrides[generic_symbol]
            info = mt5.symbol_info(ov)
            if info:
                self._ensure_selected(ov)
                self.resolved_cache[generic_symbol] = ov
                return ov

        # 2. Aday listesinden eşleme
        candidates = self.CANDIDATE_MAP.get(generic_symbol, [generic_symbol])
        for cand in candidates:
            info = mt5.symbol_info(cand)
            if info is not None:
                self._ensure_selected(cand)
                self.resolved_cache[generic_symbol] = cand
                return cand

        # 3. Son ek taraması (.pro, .raw, _i vb.)
        all_symbols = [s.name for s in mt5.symbols_get()] if mt5.symbols_get() else []
        for s_name in all_symbols:
            if s_name.upper().startswith(generic_symbol.upper()):
                self._ensure_selected(s_name)
                self.resolved_cache[generic_symbol] = s_name
                return s_name

        logger.warning(f"⚠️ Sembol MT5'te bulunamadı: {generic_symbol}")
        return None

    def _ensure_selected(self, symbol: str):
        """Sembolü MarketWatch'a (Piyasa Gözlemi) ekler."""
        info = mt5.symbol_info(symbol)
        if info and not info.visible:
            mt5.symbol_select(symbol, True)
