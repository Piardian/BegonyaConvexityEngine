import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

logger = logging.getLogger("ConvexityMacroGate")

class MacroGateEvaluation:
    def __init__(
        self,
        allowed: bool,
        action: str,
        symbol: str,
        mapped_key: str,
        direction: str,
        macro_bias: str,
        primary_regime: str,
        risk_multiplier: float,
        capital_preservation: bool,
        btc_decoupling: bool,
        status_message: str,
        rationale: str
    ):
        self.allowed = allowed
        self.action = action  # "PROCEED", "VETO", "DEFENSIVE_HOLD", "NEUTRAL_RANGE"
        self.symbol = symbol
        self.mapped_key = mapped_key
        self.direction = direction.upper()
        self.macro_bias = macro_bias
        self.primary_regime = primary_regime
        self.risk_multiplier = risk_multiplier
        self.capital_preservation = capital_preservation
        self.btc_decoupling = btc_decoupling
        self.status_message = status_message
        self.rationale = rationale

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "action": self.action,
            "symbol": self.symbol,
            "mapped_key": self.mapped_key,
            "direction": self.direction,
            "macro_bias": self.macro_bias,
            "primary_regime": self.primary_regime,
            "risk_multiplier": self.risk_multiplier,
            "capital_preservation": self.capital_preservation,
            "btc_decoupling": self.btc_decoupling,
            "status_message": self.status_message,
            "rationale": self.rationale
        }

    def __repr__(self) -> str:
        return f"<MacroGateEvaluation {self.symbol} {self.direction} -> allowed={self.allowed}, action={self.action}, risk_mult={self.risk_multiplier}x>"


class MacroGateAdapter:
    """
    Begonya Convexity Engine <-> Begonya Makro Rejim & Yönlü Kapı Köprüsü.
    Begonya'nın 'shared/macro_bias_gate.json' dosyasını okuyarak D1 trend kırılımlarını
    makroekonomik rejim, süre riski, reel faiz ve fon tasfiyeleri (decoupling) açısından denetler.
    """

    # 16 Küresel Varlığın Makro Anahtar Eşlemesi
    SYMBOL_MACRO_MAP = {
        "EURUSD": "EURUSD",
        "GBPUSD": "EURUSD",
        "AUDUSD": "COPPER_GOLD",
        "NZDUSD": "COPPER_GOLD",
        "USDCAD": "BRENT",
        "USDJPY": "US10Y",
        "USDCHF": "DXY",
        "XAUUSD": "XAUUSD",
        "SILVER": "XAUUSD",
        "CL_OIL": "BRENT",
        "NAS100": "SPX",
        "SP500": "SPX",
        "BTCUSD": "BTC",
        "ETHUSD": "BTC",
        "SOLUSD": "BTC"
    }

    # Sentetik Çapraz & Majör Kur Haritası (Emtia ve Faiz Sürücüleri)
    SYNTHETIC_CROSS_MAP = {
        "AUDCAD": {"base": "AUD", "quote": "CAD", "base_driver": "AUD", "quote_driver": "CAD"},
        "CADJPY": {"base": "CAD", "quote": "JPY", "base_driver": "CAD", "quote_driver": "JPY"},
        "GBPJPY": {"base": "GBP", "quote": "JPY", "base_driver": "GBP", "quote_driver": "JPY"},
        "AUDJPY": {"base": "AUD", "quote": "JPY", "base_driver": "AUD", "quote_driver": "JPY"},
        "EURGBP": {"base": "EUR", "quote": "GBP", "base_driver": "EUR", "quote_driver": "GBP"},
        "EURAUD": {"base": "EUR", "quote": "AUD", "base_driver": "EUR", "quote_driver": "AUD"},
        "NZDCAD": {"base": "NZD", "quote": "CAD", "base_driver": "NZD", "quote_driver": "CAD"},
        "EURJPY": {"base": "EUR", "quote": "JPY", "base_driver": "EUR", "quote_driver": "JPY"},
        "USDCAD": {"base": "USD", "quote": "CAD", "base_driver": "USD", "quote_driver": "CAD"},
        "USDJPY": {"base": "USD", "quote": "JPY", "base_driver": "USD", "quote_driver": "JPY"},
        "GBPUSD": {"base": "GBP", "quote": "USD", "base_driver": "GBP", "quote_driver": "USD"},
        "AUDUSD": {"base": "AUD", "quote": "USD", "base_driver": "AUD", "quote_driver": "USD"},
        "NZDUSD": {"base": "NZD", "quote": "USD", "base_driver": "NZD", "quote_driver": "USD"},
        "USDCHF": {"base": "USD", "quote": "CHF", "base_driver": "USD", "quote_driver": "CHF"},
        "EURCHF": {"base": "EUR", "quote": "CHF", "base_driver": "EUR", "quote_driver": "CHF"},
        "GBPCHF": {"base": "GBP", "quote": "CHF", "base_driver": "GBP", "quote_driver": "CHF"},
        "AUDCHF": {"base": "AUD", "quote": "CHF", "base_driver": "AUD", "quote_driver": "CHF"},
        "CADCHF": {"base": "CAD", "quote": "CHF", "base_driver": "CAD", "quote_driver": "CHF"},
        "NZDCHF": {"base": "NZD", "quote": "CHF", "base_driver": "NZD", "quote_driver": "CHF"},
        "CHFJPY": {"base": "CHF", "quote": "JPY", "base_driver": "CHF", "quote_driver": "JPY"},
    }

    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(MacroGateAdapter, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, custom_gate_path: Optional[str] = None):
        if self._initialized:
            return
        self.custom_gate_path = custom_gate_path
        self.gate_paths = self._resolve_gate_paths()
        self._cached_payload: Optional[Dict[str, Any]] = None
        self._cached_mtime: float = 0.0
        self._initialized = True

    def _resolve_gate_paths(self) -> list:
        paths = []
        if self.custom_gate_path:
            paths.append(Path(self.custom_gate_path))

        curr = Path(__file__).resolve().parent.parent
        # 1. Begonya ana proje kökündeki shared/macro_bias_gate.json
        begonya_root = curr.parent / "Begonya"
        paths.append(begonya_root / "shared" / "macro_bias_gate.json")
        paths.append(begonya_root / "macro_engine" / "gateways" / "macro_bias_gate.json")

        # 2. macro_multi_agi_army
        paths.append(curr.parent / "macro_multi_agi_army" / "gateways" / "macro_bias_gate.json")

        # 3. Yerel yedek
        paths.append(curr / "config" / "macro_bias_gate.json")
        return paths

    def load_gate_payload(self) -> Optional[Dict[str, Any]]:
        """Gate JSON dosyasını atomik ve önbellekli olarak okur."""
        for p in self.gate_paths:
            if p.exists():
                try:
                    mtime = p.stat().st_mtime
                    if self._cached_payload is not None and mtime == self._cached_mtime:
                        return self._cached_payload
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._cached_payload = data
                    self._cached_mtime = mtime
                    return data
                except Exception as e:
                    logger.warning(f"Gate dosyası ({p}) okunamadı: {e}")
                    continue
        return None

    def resolve_driver_score(self, driver: str, payload: Dict[str, Any]) -> Tuple[int, str]:
        rs = payload.get("regime_state", {})
        asset_biases = payload.get("asset_biases", {})
        d_upper = (driver or "").upper()

        # 1. Doğrudan hesaplanmış currency score kontrolü
        cross_scores = rs.get("cross_currency_scores", {})
        if d_upper in cross_scores and isinstance(cross_scores[d_upper], (int, float)):
            s = int(cross_scores[d_upper])
            return s, f"Rejim Skoru ({d_upper}): {'+' if s > 0 else ''}{s}"

        if d_upper in ("CAD", "CAD_MACRO", "BRENT"):
            brent_roc = rs.get("brent_roc_20d")
            ca_spread_delta = rs.get("spread_ca_us_2y_delta_5d")
            if isinstance(brent_roc, (int, float)) and isinstance(ca_spread_delta, (int, float)):
                if brent_roc > 3.0 and ca_spread_delta > 0:
                    return 1, f"Brent 20G RoC: +%{brent_roc:.1f} & CA-US 2Y Spread Genişliyor (+{ca_spread_delta:.1f} bps) -> Güçlü CAD"
                elif brent_roc < -3.0 and ca_spread_delta < 0:
                    return -1, f"Brent 20G RoC: %{brent_roc:.1f} & CA-US 2Y Spread Daralıyor ({ca_spread_delta:.1f} bps) -> Zayıf CAD"
                return 0, f"Brent RoC (%{brent_roc:.1f}) ve 2Y Spread ({ca_spread_delta:.1f} bps) dengeli/nötr"
            brent = rs.get("brent_level", 80.0)
            brent_pct = rs.get("brent_pct_60d", 50.0)
            if brent >= 85.0 or brent_pct >= 80.0:
                return 1, f"Brent petrol yüksek (${brent:.1f}) -> Güçlü CAD"
            elif brent < 72.0 or brent_pct <= 25.0:
                return -1, f"Brent petrol zayıf (${brent:.1f}) -> Zayıf CAD"
            return 0, f"Brent petrol dengeli (${brent:.1f})"

        elif d_upper in ("AUD", "AUD_MACRO", "COPPER_GOLD"):
            delta = rs.get("copper_gold_delta_4w_pct", 0.0)
            iron_ore_roc = rs.get("iron_ore_roc_20d")
            if isinstance(iron_ore_roc, (int, float)):
                if delta > 0 and iron_ore_roc > 0:
                    return 1, f"Bakır/Altın (+%{delta:.2f}) ve Demir Cevheri (+%{iron_ore_roc:.1f}) pozitif -> Güçlü AUD"
                elif delta < 0 and iron_ore_roc < 0:
                    return -1, f"Bakır/Altın (%{delta:.2f}) ve Demir Cevheri (%{iron_ore_roc:.1f}) zayıf -> Zayıf AUD"
                return 0, f"Bakır/Altın (%{delta:.2f}) ve Demir Cevheri (%{iron_ore_roc:.1f}) nötr/ayrışmış"
            if delta > 1.5:
                return 1, f"Bakır/Altın momentumu pozitif (+%{delta:.2f})"
            elif delta < -1.0:
                return -1, f"Bakır/Altın sanayi talebi zayıf (%{delta:.2f})"
            return 0, f"Bakır/Altın momentumu nötr (%{delta:.2f})"

        elif d_upper in ("NZD", "NZD_MACRO"):
            dairy_roc = rs.get("dairy_gdt_roc_20d", 0.0)
            vix = rs.get("vix_level", 15.0)
            au_nz_spread_delta = rs.get("spread_au_nz_2y_delta_5d")

            # GDT Stale Guard: İki haftalık ihale aralığında (bayat veri) AU-NZ 2Y getiri makası devralır
            if abs(dairy_roc) <= 0.01 and isinstance(au_nz_spread_delta, (int, float)):
                if au_nz_spread_delta < -3.0 and vix < 25:
                    return 1, f"GDT Yatay; AU-NZ 2Y Makası NZ Lehine Daralıyor ({au_nz_spread_delta:.1f} bps) -> Güçlü NZD"
                elif au_nz_spread_delta > 3.0:
                    return -1, f"GDT Yatay; AU-NZ 2Y Makası AU Lehine Genişliyor (+{au_nz_spread_delta:.1f} bps) -> Zayıf NZD"

            if dairy_roc > 0 and vix < 20:
                return 1, f"GDT Süt İndeksi (+%{dairy_roc:.1f}) & Asya risk iştahı açık -> Güçlü NZD"
            elif dairy_roc < 0 and vix >= 20:
                return -1, f"GDT Süt İndeksi (%{dairy_roc:.1f}) & Asya riskten kaçış -> Zayıf NZD"
            return 0, f"NZD göstergeleri dengeli (Süt: %{dairy_roc:.1f}, VIX: {vix:.1f})"

        elif d_upper in ("JPY", "JPY_MACRO", "YIELD_CARRY"):
            vix_pct = rs.get("vix_pct_60d", 50.0)
            cap_pres = payload.get("capital_preservation_mode", False)
            if cap_pres or vix_pct >= 75.0:
                return 1, "Sistemik stres/VIX yüksek -> JPY güvenli liman talebi güçlü"
            return -1, "Taşıma getirisi (carry trade) faiz avantajı -> JPY zayıf"

        elif d_upper in ("EUR", "EUR_MACRO", "EUR_ENERGY"):
            penalty = rs.get("energy_penalty_active", False)
            de_spread_delta = rs.get("spread_de_us_2y_delta_5d")
            if penalty:
                return -1, "Euro Bölgesi enerji cezası aktif -> Zayıf EUR"
            if isinstance(de_spread_delta, (int, float)) and de_spread_delta > 5.0:
                return 1, f"DE-US 2Y getiri makası lehte (+{de_spread_delta:.1f} bps) -> Güçlü EUR"
            elif isinstance(de_spread_delta, (int, float)) and de_spread_delta < -5.0:
                return -1, f"DE-US 2Y getiri makası aleyhte ({de_spread_delta:.1f} bps) -> Zayıf EUR"
            return 0, "Euro Bölgesi dengeli"

        elif d_upper in ("GBP", "GBP_MACRO"):
            gb_spread_delta = rs.get("spread_gb_us_2y_delta_5d")
            if isinstance(gb_spread_delta, (int, float)) and gb_spread_delta > 5.0:
                return 1, f"GB-US 2Y getiri makası lehte (+{gb_spread_delta:.1f} bps) -> Güçlü GBP"
            elif isinstance(gb_spread_delta, (int, float)) and gb_spread_delta < -5.0:
                return -1, f"GB-US 2Y getiri makası aleyhte ({gb_spread_delta:.1f} bps) -> Zayıf GBP"
            dxy_bias = asset_biases.get("DXY", "Neutral")
            if dxy_bias in ("Bearish", "Strong Bearish"):
                return 1, "Zayıf Dolar GBP'yi destekliyor"
            elif dxy_bias in ("Bullish", "Strong Bullish"):
                return -1, "Güçlü Dolar GBP'yi baskılıyor"
            return 0, "GBP dengeli"

        elif d_upper in ("USD", "USD_MACRO", "DXY"):
            dxy_bias = asset_biases.get("DXY", "Neutral")
            if dxy_bias in ("Bullish", "Strong Bullish"):
                return 1, "DXY yükseliş trendi / Güçlü USD"
            elif dxy_bias in ("Bearish", "Strong Bearish"):
                return -1, "DXY düşüş trendi / Zayıf USD"
            return 0, "USD dengeli / nötr"

        elif d_upper in ("CHF", "CHF_MACRO"):
            vix = rs.get("vix_level", 15.0)
            vix_pct = rs.get("vix_pct_60d", 50.0)
            cap_pres = payload.get("capital_preservation_mode", False)
            if cap_pres or vix >= 25.0 or vix_pct >= 75.0:
                return 1, "Sistemik kriz / riskten kaçışta CHF güvenli liman talebi güçlü"
            elif vix < 18.0:
                return -1, "Düşük oynaklıkta SNB faiz dezavantajı / Zayıf CHF"
            return 0, "CHF dengeli / nötr"

        return 0, "Nötr sürücü"

    def resolve_currency_legs(self, symbol: str, trade_direction: str) -> Dict[str, Any]:
        clean_sym = symbol.upper()
        dir_upper = trade_direction.upper()
        is_long = dir_upper == "LONG"
        cfg = self.SYNTHETIC_CROSS_MAP.get(clean_sym, {})
        base = cfg.get("base", clean_sym[:3] if len(clean_sym) == 6 else "")
        quote = cfg.get("quote", clean_sym[3:6] if len(clean_sym) == 6 else "")

        usd_leg = None
        jpy_leg = None
        is_crypto = base in ("BTC", "SOL", "ETH", "LTC") or clean_sym.startswith(("BTC", "SOL", "ETH"))

        if base == "USD":
            usd_leg = "LONG" if is_long else "SHORT"
        elif quote == "USD":
            usd_leg = "SHORT" if is_long else "LONG"

        if base == "JPY":
            jpy_leg = "LONG" if is_long else "SHORT"
        elif quote == "JPY":
            jpy_leg = "SHORT" if is_long else "LONG"

        return {"usd_leg": usd_leg, "jpy_leg": jpy_leg, "is_crypto": is_crypto}

    def resolve_synthetic_cross_bias(self, clean_sym: str, payload: Dict[str, Any]) -> Tuple[str, str]:
        cfg = self.SYNTHETIC_CROSS_MAP.get(clean_sym)
        if not cfg:
            return "NEUTRAL", "Sentetik harita tanımı yok"

        base_score, base_reason = self.resolve_driver_score(cfg["base_driver"], payload)
        quote_score, quote_reason = self.resolve_driver_score(cfg["quote_driver"], payload)

        net_score = base_score - quote_score
        base_sym = cfg["base"]
        quote_sym = cfg["quote"]

        if net_score > 0:
            return "LONG", f"Sentetik Çapraz: {base_sym} ({base_reason}) vs {quote_sym} ({quote_reason}) -> Net Skor: +{net_score} (LONG)"
        elif net_score < 0:
            return "SHORT", f"Sentetik Çapraz: {base_sym} ({base_reason}) vs {quote_sym} ({quote_reason}) -> Net Skor: {net_score} (SHORT)"
        return "NEUTRAL", f"Sentetik Çapraz: {base_sym} ({base_reason}) vs {quote_sym} ({quote_reason}) -> Net Skor: 0 (Nötr)"

    def evaluate_candidate(
        self,
        symbol: str,
        trade_direction: str,
        custom_payload: Optional[Dict[str, Any]] = None,
        active_positions: Optional[List[Dict[str, Any]]] = None
    ) -> MacroGateEvaluation:
        """
        D1 Donchian kırılım sinyalini makro kapıdan geçirir.
        trade_direction: "LONG" veya "SHORT"
        """
        clean_sym = symbol.upper()
        dir_clean = trade_direction.upper()
        macro_key = self.SYMBOL_MACRO_MAP.get(clean_sym, clean_sym)

        payload = custom_payload if custom_payload is not None else self.load_gate_payload()
        if not payload:
            # Failsafe: Makro verisi yoksa kontrollü 0.50x risk ile nötr devam et
            return MacroGateEvaluation(
                allowed=True,
                action="PROCEED_CAUTION",
                symbol=clean_sym,
                mapped_key=macro_key,
                direction=dir_clean,
                macro_bias="NO_DATA",
                primary_regime="Bilinmeyen / Failsafe",
                risk_multiplier=0.50,
                capital_preservation=False,
                btc_decoupling=False,
                status_message="⚠️ Makro veri bulunamadı (Failsafe 0.50x Risk)",
                rationale="shared/macro_bias_gate.json aktif değil; tedbiren yarı risk uygulandı."
            )

        regime = payload.get("primary_regime", "Genel Makro Rejim")
        risk_score = payload.get("volatility_risk_score", 0.40)
        capital_preservation = payload.get("capital_preservation_mode", False)
        btc_decoupling = payload.get("btc_decoupling_active", False)
        rationale = payload.get("macro_rationale", "")
        recommended_risk_mult = payload.get("recommended_risk_multiplier", 1.0)
        regime_state = payload.get("regime_state", {})

        gates = payload.get("execution_bias_gates", {})
        macro_bias = str(gates.get(macro_key, gates.get(clean_sym, "NEUTRAL_ALL"))).upper()

        # -------------------------------------------------------------------------
        # -1. KATMAN: PORTFÖY BETA KÜMELENME & KORELASYON KALKANI (EXPOSURE CAPS)
        # -------------------------------------------------------------------------
        if active_positions:
            candidate_legs = self.resolve_currency_legs(clean_sym, dir_clean)
            active_usd_same = 0
            active_jpy_short = 0
            active_crypto = 0

            for pos in active_positions:
                pos_sym = str(pos.get("symbol", "")).upper()
                pos_dir = str(pos.get("direction", pos.get("tradeDirection", ""))).upper()
                if pos_sym == clean_sym:
                    continue
                legs = self.resolve_currency_legs(pos_sym, pos_dir)
                if candidate_legs["usd_leg"] and legs["usd_leg"] == candidate_legs["usd_leg"]:
                    active_usd_same += 1
                if legs["jpy_leg"] == "SHORT":
                    active_jpy_short += 1
                if legs["is_crypto"]:
                    active_crypto += 1

            if candidate_legs["usd_leg"] and active_usd_same >= 2:
                return MacroGateEvaluation(
                    allowed=False,
                    action="VETO",
                    symbol=clean_sym,
                    mapped_key=macro_key,
                    direction=dir_clean,
                    macro_bias="PORTFOLIO_EXPOSURE_CAP",
                    primary_regime=regime,
                    risk_multiplier=0.0,
                    capital_preservation=capital_preservation,
                    btc_decoupling=btc_decoupling,
                    status_message=f"🛑 VETO [PORTFOLIO_EXPOSURE_CAP]: Dolar ({'Uzun/Long' if candidate_legs['usd_leg'] == 'LONG' else 'Kısa/Short'}) bacağında izin verilen maksimum aktif pozisyon sınırına (2) ulaşıldı! Portföy beta kümelenmesini önlemek için yeni Dolar işlemi engellendi.",
                    rationale=rationale
                )

            if candidate_legs["jpy_leg"] == "SHORT" and active_jpy_short >= 2:
                return MacroGateEvaluation(
                    allowed=False,
                    action="VETO",
                    symbol=clean_sym,
                    mapped_key=macro_key,
                    direction=dir_clean,
                    macro_bias="PORTFOLIO_EXPOSURE_CAP",
                    primary_regime=regime,
                    risk_multiplier=0.0,
                    capital_preservation=capital_preservation,
                    btc_decoupling=btc_decoupling,
                    status_message="🛑 VETO [PORTFOLIO_EXPOSURE_CAP]: JPY-Short (Yen Satış / Carry) bacağında maksimum aktif pozisyon sınırına (2) ulaşıldı! BoJ faiz sıçraması ve küresel kriz riskine karşı 3. JPY satışı engellendi.",
                    rationale=rationale
                )

            if candidate_legs["is_crypto"] and active_crypto >= 1:
                return MacroGateEvaluation(
                    allowed=False,
                    action="VETO",
                    symbol=clean_sym,
                    mapped_key=macro_key,
                    direction=dir_clean,
                    macro_bias="PORTFOLIO_EXPOSURE_CAP",
                    primary_regime=regime,
                    risk_multiplier=0.0,
                    capital_preservation=capital_preservation,
                    btc_decoupling=btc_decoupling,
                    status_message="🛑 VETO [PORTFOLIO_EXPOSURE_CAP]: Yüksek beta Kripto sepetinde maksimum aktif pozisyon sınırına (1) ulaşıldı! Fon tasfiyesi (margin call) dalgasına karşı ikinci bir kripto işlemi engellendi.",
                    rationale=rationale
                )

        # -------------------------------------------------------------------------
        # 0. KATMAN: KIRMIZI BÜLTEN DEVRE KESİCİSİ (RED-FOLDER EVENT FREEZE ±15 DAKİKA)
        # -------------------------------------------------------------------------
        if regime_state.get("event_freeze_active") or macro_bias == "EVENT_FREEZE":
            event_info = regime_state.get("active_event_info", "Kritik Makro Veri Açıklaması")
            return MacroGateEvaluation(
                allowed=False,
                action="VETO",
                symbol=clean_sym,
                mapped_key=macro_key,
                direction=dir_clean,
                macro_bias="EVENT_FREEZE",
                primary_regime=regime,
                risk_multiplier=0.0,
                capital_preservation=True,
                btc_decoupling=btc_decoupling,
                status_message=f"🛑 VETO [EVENT_FREEZE]: {event_info} nedeniyle tüm yeni girişler donduruldu (±15 dk haber koruması)!",
                rationale=rationale
            )

        # -------------------------------------------------------------------------
        # 1. KATMAN: SİSTEMİK AŞIRI KRİZ DEVRE KESİCİSİ (RİSK SKORU >= 0.95)
        # -------------------------------------------------------------------------
        if risk_score >= 0.95:
            return MacroGateEvaluation(
                allowed=False,
                action="VETO",
                symbol=clean_sym,
                mapped_key=macro_key,
                direction=dir_clean,
                macro_bias=macro_bias,
                primary_regime=regime,
                risk_multiplier=0.0,
                capital_preservation=True,
                btc_decoupling=btc_decoupling,
                status_message=f"🛑 VETO: Sistemik Aşırı Kriz / Acil Devre Kesici Devrede (Risk Skoru: {risk_score:.2f})",
                rationale=rationale
            )

        # -------------------------------------------------------------------------
        # 2. KATMAN: ASİMETRİK ENSTRÜMAN MUTLAK KALKANLARI
        # -------------------------------------------------------------------------
        # A) ALTIN / GÜMÜŞ (XAUUSD / SILVER) SHORT YASAĞI (Mali Hakimiyet Kalkanı)
        if clean_sym in ("XAUUSD", "SILVER") and dir_clean == "SHORT":
            is_cash_dash = risk_score >= 0.90 and "Deflationary" in regime
            if not is_cash_dash:
                return MacroGateEvaluation(
                    allowed=False,
                    action="VETO",
                    symbol=clean_sym,
                    mapped_key=macro_key,
                    direction=dir_clean,
                    macro_bias=macro_bias,
                    primary_regime=regime,
                    risk_multiplier=0.0,
                    capital_preservation=capital_preservation,
                    btc_decoupling=btc_decoupling,
                    status_message="🛑 VETO: Mali Hakimiyet Çağında Altında SHORT Kesinlikle Yasaktır (Fiziki Rezerv Talebi / Egemen Borç Kalkanı)!",
                    rationale=rationale
                )

        # B) KRİPTO (BTC / ETH / SOL) TAHVİL ŞOKU DECOUPLING LONG YASAĞI
        if clean_sym in ("BTCUSD", "ETHUSD", "SOLUSD") and dir_clean == "LONG" and btc_decoupling:
            return MacroGateEvaluation(
                allowed=False,
                action="VETO",
                symbol=clean_sym,
                mapped_key=macro_key,
                direction=dir_clean,
                macro_bias=macro_bias,
                primary_regime=regime,
                risk_multiplier=0.0,
                capital_preservation=capital_preservation,
                btc_decoupling=True,
                status_message="🛑 VETO: Tahvil Şoku Kaynaklı Fon Tasfiye Dalgası (Margin Call) Devrede; Kripto Long Yasak!",
                rationale=rationale
            )

        # C) SOL (SOLUSD / SOL) KURALI: BTC Gate ve SOL/BTC Göreli Güç Filtresi
        if clean_sym in ("SOLUSD", "SOL") and dir_clean == "LONG":
            btc_gate = str(gates.get("BTC", gates.get("BTCUSD", ""))).upper()
            sol_btc_bullish = regime_state.get("sol_btc_structure_bullish", (regime_state.get("sol_btc_roc_5d", 0) > 0))
            sol_4h_broken = regime_state.get("sol_btc_4h_structure_broken", False)
            is_btc_long = (btc_gate in ("LONG_ONLY", "LONG_ONLY_ALLOWED_IF_DEBASEMENT")) and not btc_decoupling
            if not is_btc_long:
                return MacroGateEvaluation(
                    allowed=False,
                    action="VETO",
                    symbol=clean_sym,
                    mapped_key=macro_key,
                    direction=dir_clean,
                    macro_bias=macro_bias,
                    primary_regime=regime,
                    risk_multiplier=0.0,
                    capital_preservation=capital_preservation,
                    btc_decoupling=btc_decoupling,
                    status_message=f"🛑 VETO: SOL Long İzni Yok! BTC Kapısı LONG_ONLY değil ({btc_gate or 'NÖTR'}) veya Decoupling devrede.",
                    rationale=rationale
                )
            if sol_4h_broken:
                return MacroGateEvaluation(
                    allowed=False,
                    action="VETO",
                    symbol=clean_sym,
                    mapped_key=macro_key,
                    direction=dir_clean,
                    macro_bias="DEFENSIVE_HOLD",
                    primary_regime=regime,
                    risk_multiplier=0.0,
                    capital_preservation=capital_preservation,
                    btc_decoupling=btc_decoupling,
                    status_message="🛑 VETO: SOL Long İzni Yok! SOL/BTC 4H swing dip kırıldı (CHoCH / Yapı Bozuldu - DEFENSIVE_HOLD)!",
                    rationale=rationale
                )
            if not sol_btc_bullish:
                return MacroGateEvaluation(
                    allowed=False,
                    action="VETO",
                    symbol=clean_sym,
                    mapped_key=macro_key,
                    direction=dir_clean,
                    macro_bias=macro_bias,
                    primary_regime=regime,
                    risk_multiplier=0.0,
                    capital_preservation=capital_preservation,
                    btc_decoupling=btc_decoupling,
                    status_message=f"🛑 VETO: SOL Long İzni Yok! SOL/BTC momentumu negatif (%{regime_state.get('sol_btc_roc_5d', 0)}).",
                    rationale=rationale
                )

        # C) ENDEKSLER (NAS100 / SP500)
        if clean_sym in ("NAS100", "SP500") and dir_clean == "SHORT":
            vix_pct = regime_state.get("vix_pct_60d", 50.0)
            if risk_score >= 0.65 or vix_pct >= 80.0:
                return MacroGateEvaluation(
                    allowed=False,
                    action="VETO",
                    symbol=clean_sym,
                    mapped_key=macro_key,
                    direction=dir_clean,
                    macro_bias=macro_bias,
                    primary_regime=regime,
                    risk_multiplier=0.0,
                    capital_preservation=capital_preservation,
                    btc_decoupling=btc_decoupling,
                    status_message="🛑 VETO: Endekslerde VIX Yüksek (Gecikilmiş Short; Ayı Piyasası Rallisi / Short Squeeze Riski)!",
                    rationale=rationale
                )

        # -------------------------------------------------------------------------
        # 3. KATMAN: MAKRO YÖN ÇÖZÜMLEME & DOĞRU ORANTILILIK DENETİMİ (STRICT ALIGNMENT)
        # -------------------------------------------------------------------------
        asset_biases = payload.get("asset_biases", {})
        macro_dir = "NEUTRAL"
        cross_reason = ""
        is_synthetic_cross = clean_sym in self.SYNTHETIC_CROSS_MAP

        if is_synthetic_cross:
            macro_dir, cross_reason = self.resolve_synthetic_cross_bias(clean_sym, payload)
            macro_bias = macro_dir
        elif macro_bias == "LONG_ONLY":
            macro_dir = "LONG"
        elif macro_bias == "SHORT_ONLY":
            macro_dir = "SHORT"
        elif macro_bias in ("DEFENSIVE_HOLD", "NO_TRADE", "REDUCE_ONLY"):
            macro_dir = "DEFENSIVE"
        else:
            # Asset biases üzerinden yön kontrolü
            raw_bias = asset_biases.get(macro_key, asset_biases.get(clean_sym))
            if raw_bias in ("Strong Bullish", "Bullish"):
                macro_dir = "LONG"
            elif raw_bias in ("Strong Bearish", "Bearish"):
                macro_dir = "SHORT"

        # A) Zıt Yön Kontrolü
        if dir_clean == "LONG" and (macro_dir == "SHORT" or macro_bias in ("SHORT_ONLY", "SHORT")):
            veto_msg = (
                f"🛑 VETO: {clean_sym} Makro Yönü SHORT iken LONG Açılamaz ({cross_reason})!"
                if is_synthetic_cross
                else f"🛑 VETO: {clean_sym} Makro Yönü SHORT_ONLY iken LONG Açılamaz (Ters orantılı)!"
            )
            return MacroGateEvaluation(
                allowed=False,
                action="VETO",
                symbol=clean_sym,
                mapped_key=macro_key,
                direction=dir_clean,
                macro_bias=macro_bias,
                primary_regime=regime,
                risk_multiplier=0.0,
                capital_preservation=capital_preservation,
                btc_decoupling=btc_decoupling,
                status_message=veto_msg,
                rationale=rationale
            )

        if dir_clean == "SHORT" and (macro_dir == "LONG" or macro_bias in ("LONG_ONLY", "LONG")):
            veto_msg = (
                f"🛑 VETO: {clean_sym} Makro Yönü LONG iken SHORT Açılamaz ({cross_reason})!"
                if is_synthetic_cross
                else f"🛑 VETO: {clean_sym} Makro Yönü LONG_ONLY iken SHORT Açılamaz (Ters orantılı)!"
            )
            return MacroGateEvaluation(
                allowed=False,
                action="VETO",
                symbol=clean_sym,
                mapped_key=macro_key,
                direction=dir_clean,
                macro_bias=macro_bias,
                primary_regime=regime,
                risk_multiplier=0.0,
                capital_preservation=capital_preservation,
                btc_decoupling=btc_decoupling,
                status_message=veto_msg,
                rationale=rationale
            )

        # B) DEFENSIVE_HOLD Kontrolü
        if macro_bias == "DEFENSIVE_HOLD" or macro_dir == "DEFENSIVE":
            return MacroGateEvaluation(
                allowed=False,
                action="VETO",
                symbol=clean_sym,
                mapped_key=macro_key,
                direction=dir_clean,
                macro_bias=macro_bias,
                primary_regime=regime,
                risk_multiplier=0.0,
                capital_preservation=capital_preservation,
                btc_decoupling=btc_decoupling,
                status_message=f"🛑 VETO: {clean_sym} Makro Savunma Modunda (DEFENSIVE_HOLD) - Yeni Giriş Yasak!",
                rationale=rationale
            )

        # C) Nötr / Yönsüz Varlık Veto Kuralı
        # Kullanıcı talebi: "yalnızca makro ekonomiyle doğru orantıda olan işlemleri versin, diğerlerini veto etsin"
        if macro_dir == "NEUTRAL":
            veto_msg = (
                f"🛑 VETO: {clean_sym} için makroekonomik yön nötr / yönsüzdür ({cross_reason}). Yalnızca makro ile doğru orantılı işlemlere izin verilir!"
                if is_synthetic_cross
                else f"🛑 VETO: {clean_sym} için makroekonomik yön nötr / yönsüzdür ({macro_bias}). Yalnızca makro ile doğru orantılı işlemlere izin verilir!"
            )
            return MacroGateEvaluation(
                allowed=False,
                action="VETO",
                symbol=clean_sym,
                mapped_key=macro_key,
                direction=dir_clean,
                macro_bias=macro_bias,
                primary_regime=regime,
                risk_multiplier=0.0,
                capital_preservation=capital_preservation,
                btc_decoupling=btc_decoupling,
                status_message=veto_msg,
                rationale=rationale
            )

        # -------------------------------------------------------------------------
        # 4. KATMAN: MAKRO ONAYLANDI (DİNAMİK RİSK ÇARPANLI DOĞRU ORANTILI GİRİŞ)
        # -------------------------------------------------------------------------
        final_mult = max(0.10, min(1.0, recommended_risk_mult))
        status_msg = (
            f"🌟 ONAYLANDI: Sentetik Çapraz Makro Teyidi ({cross_reason}) -> {final_mult:.2f}x Risk Uygula"
            if is_synthetic_cross
            else f"🌟 ONAYLANDI: Makro Rejimle Doğru Orantılı ({macro_bias}) -> {final_mult:.2f}x Risk Uygula"
        )
        return MacroGateEvaluation(
            allowed=True,
            action="PROCEED",
            symbol=clean_sym,
            mapped_key=macro_key,
            direction=dir_clean,
            macro_bias=macro_bias,
            primary_regime=regime,
            risk_multiplier=final_mult,
            capital_preservation=capital_preservation,
            btc_decoupling=btc_decoupling,
            status_message=status_msg,
            rationale=rationale
        )
