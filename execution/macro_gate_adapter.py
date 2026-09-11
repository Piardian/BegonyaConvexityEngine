import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

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

    def evaluate_candidate(
        self,
        symbol: str,
        trade_direction: str
    ) -> MacroGateEvaluation:
        """
        D1 Donchian kırılım sinyalini makro kapıdan geçirir.
        trade_direction: "LONG" veya "SHORT"
        """
        clean_sym = symbol.upper()
        dir_clean = trade_direction.upper()
        macro_key = self.SYMBOL_MACRO_MAP.get(clean_sym, clean_sym)

        payload = self.load_gate_payload()
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
        # 1. KATMAN: SİSTEMİK KRİZ & SERMAYE KORUMA KALKANI
        # -------------------------------------------------------------------------
        if capital_preservation or risk_score >= 0.90:
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
                status_message=f"🛑 VETO: Sistemik Kriz / Sermaye Koruma Kalkanı Devrede (Risk Skoru: {risk_score:.2f})",
                rationale=rationale
            )

        # -------------------------------------------------------------------------
        # 2. KATMAN: DEFENSIVE_HOLD KONTROLÜ
        # -------------------------------------------------------------------------
        if macro_bias == "DEFENSIVE_HOLD":
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

        # -------------------------------------------------------------------------
        # 3. KATMAN: YÖNLÜ UYUM (DIRECTIONAL GATE)
        # -------------------------------------------------------------------------
        if dir_clean == "SHORT" and macro_bias == "LONG_ONLY":
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
                status_message=f"🛑 VETO: {clean_sym} Makro Yönü LONG_ONLY iken SHORT Açılamaz!",
                rationale=rationale
            )

        if dir_clean == "LONG" and macro_bias == "SHORT_ONLY":
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
                status_message=f"🛑 VETO: {clean_sym} Makro Yönü SHORT_ONLY iken LONG Açılamaz!",
                rationale=rationale
            )

        # -------------------------------------------------------------------------
        # 4. KATMAN: ASİMETRİK ENSTRÜMAN KURALLARI
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
                    status_message="🛑 VETO: Mali Hakimiyet Çağında Altın/Gümüşte SHORT Kesinlikle Yasaktır!",
                    rationale=rationale
                )

        # B) ALTINDA SÜRE RİSKİ / REEL FAİZ ŞOKU (NEUTRAL_RANGE İse Düşen Bıçak Longlanmaz)
        if clean_sym in ("XAUUSD", "SILVER") and dir_clean == "LONG" and macro_bias == "NEUTRAL_RANGE":
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
                status_message="🛑 VETO: 10Y Reel Faiz >= %1.90 & Süre Riski Sebebiyle Altında NEUTRAL_RANGE Devrede; Yeni Long Kilitlendi!",
                rationale=rationale
            )

        # C) KRİPTO (BTC / ETH / SOL) TAHVİL ŞOKU DECOUPLING LONG YASAĞI
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

        # D) ENDEKSLER (NAS100 / SP500)
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

        if clean_sym in ("NAS100", "SP500") and dir_clean == "LONG" and macro_bias == "NEUTRAL_RANGE":
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
                status_message="🛑 VETO: Higher for Longer & Bear Steepening İskonto Şoku; Endekslerde Yeni Long Kilitlendi!",
                rationale=rationale
            )

        # -------------------------------------------------------------------------
        # 5. KATMAN: MAKRO ONAYLANDI (PROCEED)
        # -------------------------------------------------------------------------
        action = "PROCEED"
        final_mult = max(0.10, min(1.0, recommended_risk_mult))
        if macro_bias == "NEUTRAL_RANGE":
            final_mult = min(final_mult, 0.40)
            action = "NEUTRAL_RANGE"

        return MacroGateEvaluation(
            allowed=True,
            action=action,
            symbol=clean_sym,
            mapped_key=macro_key,
            direction=dir_clean,
            macro_bias=macro_bias,
            primary_regime=regime,
            risk_multiplier=final_mult,
            capital_preservation=False,
            btc_decoupling=btc_decoupling,
            status_message=f"✅ ONAYLANDI: Makro Rejim Uyumlu ({macro_bias}) -> {final_mult:.2f}x Risk Uygula",
            rationale=rationale
        )
