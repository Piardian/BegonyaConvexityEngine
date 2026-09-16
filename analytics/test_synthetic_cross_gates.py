import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add project root to sys.path
PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from execution.macro_gate_adapter import MacroGateAdapter

def test_synthetic_cross_gates():
    print("=" * 70)
    print(" 🧪 BEGONYA CONVEXITY ENGINE: SENTETİK ÇAPRAZ KAPI DOĞRULAMA TESTİ 🧪 ")
    print("=" * 70)

    adapter = MacroGateAdapter()

    # 1. AUDCAD SHORT TESTİ:
    # Brent petrol ve getiri makasıyla CAD güçlü (+1), Madenler nötr (0 AUD) -> Net: AUD (0) - CAD (+1) = -1 (SHORT).
    # AUDCAD SHORT -> ALLOWED (PROCEED)
    print("\n[TEST 1] AUDCAD SHORT:")
    audcad_short_payload = {
        "primary_regime": "Late-Cycle Overheating",
        "volatility_risk_score": 0.35,
        "capital_preservation_mode": False,
        "btc_decoupling_active": False,
        "recommended_risk_multiplier": 0.25,
        "execution_bias_gates": {},
        "regime_state": {
            "cross_currency_scores": {"AUD": 0, "CAD": 1, "EUR": -1, "GBP": 0}
        }
    }
    eval_short = adapter.evaluate_candidate("AUDCAD", "SHORT", custom_payload=audcad_short_payload)
    print(f"  • İzin: {eval_short.allowed} | Eylem: {eval_short.action}")
    print(f"  • Makro Bias: {eval_short.macro_bias} | Risk Çarpanı: {eval_short.risk_multiplier}x")
    print(f"  • Mesaj: {eval_short.status_message}")
    assert eval_short.allowed is True, "AUDCAD SHORT onaylanmalı!"
    assert eval_short.action == "PROCEED", "Aksiyon PROCEED olmalı!"
    assert eval_short.macro_bias == "SHORT", "Makro bias SHORT olmalı!"
    print("  -> ✅ TEST 1 BAŞARILI: AUDCAD SHORT makro ile doğru orantılı olarak onaylandı.")

    # 2. AUDCAD LONG TESTİ:
    # Makro SHORT iken gelen LONG sinyali VETO edilmeli!
    print("\n[TEST 2] AUDCAD LONG:")
    eval_long = adapter.evaluate_candidate("AUDCAD", "LONG", custom_payload=audcad_short_payload)
    print(f"  • İzin: {eval_long.allowed} | Eylem: {eval_long.action}")
    print(f"  • Makro Bias: {eval_long.macro_bias}")
    print(f"  • Mesaj: {eval_long.status_message}")
    assert eval_long.allowed is False, "AUDCAD LONG veto edilmeli!"
    assert eval_long.action == "VETO", "Aksiyon VETO olmalı!"
    assert "SHORT iken LONG Açılamaz" in eval_long.status_message, "Zıt yön veto mesajı içermeli!"
    print("  -> ✅ TEST 2 BAŞARILI: AUDCAD LONG zıt yön olduğu için başarıyla veto edildi.")

    # 3. EURGBP TESTİ:
    # Euro Bölgesi enerji cezası aktif -> EUR zayıf (-1) vs GBP (0) -> Net: -1 (SHORT)
    print("\n[TEST 3] EURGBP SHORT:")
    eval_eurgbp = adapter.evaluate_candidate("EURGBP", "SHORT", custom_payload=audcad_short_payload)
    print(f"  • İzin: {eval_eurgbp.allowed} | Eylem: {eval_eurgbp.action}")
    print(f"  • Mesaj: {eval_eurgbp.status_message}")
    assert eval_eurgbp.allowed is True, "EURGBP SHORT onaylanmalı!"
    print("  -> ✅ TEST 3 BAŞARILI: EURGBP enerji cezası sebebiyle SHORT onaylandı.")

    # 4. SOLUSD LONG VETO TESTİ:
    # BTC Decoupling / DEFENSIVE_HOLD iken SOL Long VETO edilmeli!
    print("\n[TEST 4] SOLUSD LONG:")
    sol_decouple_payload = {
        "primary_regime": "Late-Cycle Overheating",
        "volatility_risk_score": 0.40,
        "capital_preservation_mode": False,
        "btc_decoupling_active": True,
        "execution_bias_gates": {"BTC": "DEFENSIVE_HOLD"},
        "regime_state": {"btc_decoupling_active": True}
    }
    eval_sol = adapter.evaluate_candidate("SOLUSD", "LONG", custom_payload=sol_decouple_payload)
    print(f"  • İzin: {eval_sol.allowed} | Eylem: {eval_sol.action}")
    print(f"  • Mesaj: {eval_sol.status_message}")
    assert eval_sol.allowed is False, "SOLUSD LONG veto edilmeli!"
    assert eval_sol.action == "VETO", "Aksiyon VETO olmalı!"
    print("  -> ✅ TEST 4 BAŞARILI: SOLUSD yüksek beta kripto kuralı uyarınca başarıyla veto edildi.")

    # 5. NZDCAD MANDIRA & PETROL AYRIŞMA TESTİ (Dinamik Payload):
    print("\n[TEST 5] NZDCAD DİNAMİK MANDIRA TESTİ:")
    custom_payload = {
        "primary_regime": "Reflationary Growth",
        "volatility_risk_score": 0.35,
        "capital_preservation_mode": False,
        "btc_decoupling_active": False,
        "execution_bias_gates": {},
        "regime_state": {
            "dairy_gdt_roc_20d": 6.5,
            "brent_roc_20d": -4.0,
            "spread_ca_us_2y_delta_5d": -5.0,
            "vix_level": 14.0
        }
    }
    dir_nzd, reason_nzd = adapter.resolve_synthetic_cross_bias("NZDCAD", custom_payload)
    print(f"  • NZDCAD Yön: {dir_nzd} | Gerekçe: {reason_nzd}")
    assert dir_nzd == "LONG", "GDT pozitif ve Petrol zayıfken NZDCAD LONG olmalı!"
    print("  -> ✅ TEST 5 BAŞARILI: NZDCAD mandıra ayrışması ve 2Y spread ile başarıyla LONG çözüldü.")

    # 6. GDT BAYAT VERİ (STALE DATA) KORUMASI TESTİ (AU-NZ 2Y Spread Devralması):
    print("\n[TEST 6] GDT BAYAT VERİ KORUMASI (AU-NZ SPREAD FALLBACK):")
    stale_gdt_payload = {
        "primary_regime": "Reflationary Growth",
        "volatility_risk_score": 0.35,
        "capital_preservation_mode": False,
        "btc_decoupling_active": False,
        "execution_bias_gates": {},
        "regime_state": {
            "dairy_gdt_roc_20d": 0.0,  # 14 günlük sabit ihale penceresi
            "spread_au_nz_2y_delta_5d": -6.2,  # NZD 2Y lehine sert daralma (> 3 bps)
            "brent_roc_20d": -4.0,
            "spread_ca_us_2y_delta_5d": -5.0,
            "vix_level": 15.0
        }
    }
    dir_stale, reason_stale = adapter.resolve_synthetic_cross_bias("NZDCAD", stale_gdt_payload)
    print(f"  • NZDCAD Yön: {dir_stale} | Gerekçe: {reason_stale}")
    assert dir_stale == "LONG", "GDT yatayken AU-NZ spread daralması NZD'yi LONG yapmalı!"
    assert "AU-NZ 2Y Makası" in reason_stale, "AU-NZ makası gerekçede yer almalı!"
    print("  -> ✅ TEST 6 BAŞARILI: GDT bayat veri penceresinde AU-NZ 2Y spread başarıyla tetiklendi.")

    # 7. SOL/BTC 4H SWING LOW KIRILIMI (CHoCH) TESTİ:
    print("\n[TEST 7] SOL/BTC 4H SWING LOW CHoCH KORUMASI:")
    sol_choch_payload = {
        "primary_regime": "Reflationary Growth",
        "volatility_risk_score": 0.40,
        "capital_preservation_mode": False,
        "btc_decoupling_active": False,
        "execution_bias_gates": {"BTC": "LONG_ONLY"},
        "regime_state": {
            "sol_btc_roc_5d": 6.5,  # 5 günlük getiri pozitif ancak
            "sol_btc_4h_structure_broken": True,  # 4H dip kırılmış!
            "sol_btc_structure_bullish": True
        }
    }
    eval_sol_choch = adapter.evaluate_candidate("SOLUSD", "LONG", custom_payload=sol_choch_payload)
    print(f"  • İzin: {eval_sol_choch.allowed} | Eylem: {eval_sol_choch.action}")
    print(f"  • Mesaj: {eval_sol_choch.status_message}")
    assert eval_sol_choch.allowed is False, "4H swing dip kırılınca SOL Long veto edilmeli!"
    assert "4H swing dip kırıldı" in eval_sol_choch.status_message, "4H CHoCH mesajı yer almalı!"
    print("  -> ✅ TEST 7 BAŞARILI: SOL/BTC 4H dip kırılımı gecikmeli RoC'yi ezerek SOL Long'u kilitledi.")

    # 8. KIRMIZI BÜLTEN DEVRE KESİCİSİ (RED-FOLDER EVENT FREEZE) TESTİ:
    print("\n[TEST 8] KIRMIZI BÜLTEN DEVRE KESİCİSİ (±15 DAKİKA DONDURMA):")
    event_freeze_payload = {
        "primary_regime": "Reflationary Growth",
        "volatility_risk_score": 0.40,
        "capital_preservation_mode": False,
        "btc_decoupling_active": False,
        "execution_bias_gates": {"EURUSD": "LONG_ONLY", "AUDCAD": "SHORT_ONLY"},
        "regime_state": {
            "event_freeze_active": True,
            "active_event_info": "ABD Çekirdek TÜFE (Core CPI) Yayını (T-4 dk)"
        }
    }
    eval_freeze = adapter.evaluate_candidate("EURUSD", "LONG", custom_payload=event_freeze_payload)
    print(f"  • İzin: {eval_freeze.allowed} | Eylem: {eval_freeze.action}")
    print(f"  • Mesaj: {eval_freeze.status_message}")
    assert eval_freeze.allowed is False, "Haber freeze aktifken yeni giriş veto edilmeli!"
    assert eval_freeze.macro_bias == "EVENT_FREEZE", "Makro bias EVENT_FREEZE olmalı!"
    assert "EVENT_FREEZE" in eval_freeze.status_message, "Event freeze mesajı yer almalı!"
    print("  -> ✅ TEST 8 BAŞARILI: Kırmızı bülten devre kesicisi tüm yeni emirleri başarıyla kilitledi.")

    # 9. USDCAD MAJÖR GÖRELİ DEĞER TESTİ:
    print("\n[TEST 9] USDCAD MAJÖR GÖRELİ DEĞER TESTİ:")
    usdcad_payload = {
        "primary_regime": "Late-Cycle Overheating",
        "volatility_risk_score": 0.35,
        "capital_preservation_mode": False,
        "btc_decoupling_active": False,
        "execution_bias_gates": {},
        "regime_state": {
            "cross_currency_scores": {"USD": 1, "CAD": -1}
        }
    }
    eval_usdcad = adapter.evaluate_candidate("USDCAD", "LONG", custom_payload=usdcad_payload)
    print(f"  • İzin: {eval_usdcad.allowed} | Eylem: {eval_usdcad.action} | Makro Bias: {eval_usdcad.macro_bias}")
    print(f"  • Mesaj: {eval_usdcad.status_message}")
    assert eval_usdcad.allowed is True, "USDCAD LONG onaylanmalı!"
    assert eval_usdcad.action == "PROCEED", "Aksiyon PROCEED olmalı!"
    print("  -> ✅ TEST 9 BAŞARILI: USDCAD majör paritesi kurumsal makro teyidiyle onaylandı.")

    # 10. USDJPY MAJÖR CARRY TRADE TESTİ:
    print("\n[TEST 10] USDJPY MAJÖR CARRY TRADE TESTİ:")
    usdjpy_payload = {
        "primary_regime": "Reflationary Growth",
        "volatility_risk_score": 0.35,
        "capital_preservation_mode": False,
        "btc_decoupling_active": False,
        "execution_bias_gates": {},
        "regime_state": {
            "cross_currency_scores": {"USD": 1, "JPY": -1}
        }
    }
    eval_usdjpy = adapter.evaluate_candidate("USDJPY", "LONG", custom_payload=usdjpy_payload)
    print(f"  • İzin: {eval_usdjpy.allowed} | Eylem: {eval_usdjpy.action} | Makro Bias: {eval_usdjpy.macro_bias}")
    assert eval_usdjpy.allowed is True, "USDJPY LONG onaylanmalı!"
    print("  -> ✅ TEST 10 BAŞARILI: USDJPY carry trade faiz üstünlüğüyle LONG onaylandı.")

    # 11. PORTFÖY KORELASYON KALKANI (DOLAR TAVANI) TESTİ:
    print("\n[TEST 11] PORTFÖY KORELASYON KALKANI (MAX 2 USD):")
    active_usd_positions = [
        {"symbol": "EURUSD", "direction": "SHORT"},  # USD LONG
        {"symbol": "GBPUSD", "direction": "SHORT"}   # USD LONG
    ]
    eval_usd_cap = adapter.evaluate_candidate(
        "USDCAD", "LONG", custom_payload=usdcad_payload, active_positions=active_usd_positions
    )
    print(f"  • İzin: {eval_usd_cap.allowed} | Eylem: {eval_usd_cap.action}")
    print(f"  • Mesaj: {eval_usd_cap.status_message}")
    assert eval_usd_cap.allowed is False, "2 USD-Long varken 3. USD-Long veto edilmeli!"
    assert "PORTFOLIO_EXPOSURE_CAP" in eval_usd_cap.status_message, "Exposure cap mesajı yer almalı!"
    print("  -> ✅ TEST 11 BAŞARILI: 3. Dolar pozisyonu portföy korelasyon tavanı ile engellendi.")

    # 12. PORTFÖY KORELASYON KALKANI (JPY SHORT TAVANI) TESTİ:
    print("\n[TEST 12] PORTFÖY KORELASYON KALKANI (MAX 2 JPY SHORT):")
    active_jpy_positions = [
        {"symbol": "CADJPY", "direction": "LONG"},  # JPY SHORT
        {"symbol": "AUDJPY", "direction": "LONG"}   # JPY SHORT
    ]
    gbpjpy_payload = {
        "primary_regime": "Reflationary Growth",
        "volatility_risk_score": 0.35,
        "capital_preservation_mode": False,
        "btc_decoupling_active": False,
        "execution_bias_gates": {},
        "regime_state": {"cross_currency_scores": {"GBP": 1, "JPY": -1}}
    }
    eval_jpy_cap = adapter.evaluate_candidate(
        "GBPJPY", "LONG", custom_payload=gbpjpy_payload, active_positions=active_jpy_positions
    )
    print(f"  • İzin: {eval_jpy_cap.allowed} | Eylem: {eval_jpy_cap.action}")
    print(f"  • Mesaj: {eval_jpy_cap.status_message}")
    assert eval_jpy_cap.allowed is False, "2 JPY-Short varken 3. JPY-Short veto edilmeli!"
    assert "JPY-Short" in eval_jpy_cap.status_message, "JPY-Short cap mesajı yer almalı!"
    print("  -> ✅ TEST 12 BAŞARILI: 3. JPY satışı portföy kümelenmesini önlemek için engellendi.")

    # 13. PORTFÖY KORELASYON KALKANI (KRİPTO TAVANI) TESTİ:
    print("\n[TEST 13] PORTFÖY KORELASYON KALKANI (MAX 1 CRYPTO):")
    active_crypto_positions = [
        {"symbol": "BTCUSD", "direction": "LONG"}  # BTC LONG
    ]
    sol_payload = {
        "primary_regime": "Reflationary Growth",
        "volatility_risk_score": 0.35,
        "capital_preservation_mode": False,
        "btc_decoupling_active": False,
        "execution_bias_gates": {"BTC": "LONG_ONLY", "SOL": "LONG_ONLY"},
        "regime_state": {"sol_btc_roc_5d": 5.0, "sol_btc_structure_bullish": True}
    }
    eval_crypto_cap = adapter.evaluate_candidate(
        "SOLUSD", "LONG", custom_payload=sol_payload, active_positions=active_crypto_positions
    )
    print(f"  • İzin: {eval_crypto_cap.allowed} | Eylem: {eval_crypto_cap.action}")
    print(f"  • Mesaj: {eval_crypto_cap.status_message}")
    assert eval_crypto_cap.allowed is False, "BTC Long varken SOL Long veto edilmeli!"
    assert "Kripto" in eval_crypto_cap.status_message, "Kripto cap mesajı yer almalı!"
    print("  -> ✅ TEST 13 BAŞARILI: İkinci kripto pozisyonu fon tasfiye riskine karşı engellendi.")

    # 14. EURCHF ÇAPRAZ GÖRELİ DEĞER TESTİ:
    print("\n[TEST 14] EURCHF ÇAPRAZ GÖRELİ DEĞER TESTİ:")
    eurchf_payload = {
        "primary_regime": "Late-Cycle Overheating",
        "volatility_risk_score": 0.50,
        "capital_preservation_mode": False,
        "btc_decoupling_active": False,
        "execution_bias_gates": {},
        "regime_state": {"cross_currency_scores": {"EUR": -1, "CHF": 1}}
    }
    eval_eurchf = adapter.evaluate_candidate("EURCHF", "SHORT", custom_payload=eurchf_payload)
    print(f"  • İzin: {eval_eurchf.allowed} | Eylem: {eval_eurchf.action} | Makro Bias: {eval_eurchf.macro_bias}")
    assert eval_eurchf.allowed is True, "EURCHF SHORT onaylanmalı!"
    print("  -> ✅ TEST 14 BAŞARILI: EURCHF güvenli liman CHF lehine SHORT onaylandı.")

    # 15. CHFJPY ÇAPRAZ GÖRELİ DEĞER TESTİ:
    print("\n[TEST 15] CHFJPY ÇAPRAZ GÖRELİ DEĞER TESTİ:")
    chfjpy_payload = {
        "primary_regime": "Late-Cycle Overheating",
        "volatility_risk_score": 0.50,
        "capital_preservation_mode": False,
        "btc_decoupling_active": False,
        "execution_bias_gates": {},
        "regime_state": {"cross_currency_scores": {"CHF": 1, "JPY": -1}}
    }
    eval_chfjpy = adapter.evaluate_candidate("CHFJPY", "LONG", custom_payload=chfjpy_payload)
    print(f"  • İzin: {eval_chfjpy.allowed} | Eylem: {eval_chfjpy.action} | Makro Bias: {eval_chfjpy.macro_bias}")
    assert eval_chfjpy.allowed is True, "CHFJPY LONG onaylanmalı!"
    print("  -> ✅ TEST 15 BAŞARILI: CHFJPY faiz ve sığınak farkıyla LONG onaylandı.")

    print("\n" + "=" * 70)
    print(" 🎉 TÜM SENTETİK ÇAPRAZ, MAJÖR & PORTFÖY KORUMA TESTLERİ BAŞARIYLA GEÇTİ! 🎉 ")
    print("=" * 70)

if __name__ == "__main__":
    test_synthetic_cross_gates()
