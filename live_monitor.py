import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

# Windows UTF-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from data.market_loader import MarketLoader
from engine.trend_detector import TrendDetector
from engine.live_scanner import LiveConvexityScanner
from engine.rotational_engine import RotationalSelectionEngine
from execution.telemetry_logger import TelemetryLogger

logging.basicConfig(level=logging.WARNING)

def format_begonya_card(item: dict, capital: float = 10000.0, risk_pct: float = 2.0) -> str:
    """Begonya formatında kurumsal sinyal/takip kartı oluşturur."""
    sym = item["symbol"]
    direction = item["direction"]
    status = item["status"]
    regime = item["regime"]
    macro_regime = item.get("macro_regime", "Bilinmiyor")
    macro_bias = item.get("macro_bias", "N/A")
    macro_status = item.get("macro_status_message", "")
    macro_mult = item.get("macro_risk_multiplier", 1.0)
    curr = item["current_price"]
    base_risk_dollars = capital * (risk_pct / 100.0)
    actual_risk_dollars = round(base_risk_dollars * macro_mult, 2)

    sep = "═" * 70
    thin = "─" * 70

    if item["type"] == "FRESH_BREAKOUT":
        entry = item["entry_price"]
        sl = item["stop_loss"]
        tp2 = item["partial_tp_2r"]
        is_allowed = item.get("macro_allowed", True)

        action_text = (
            f"✅ Makro kapı onayladı ({macro_mult:.2f}x). İki bilet emrini ilet."
            if is_allowed
            else f"🛑 Begonya Makro Kalkanı Veto Etti! İşlem açma, nakitte kal."
        )

        lines = [
            sep,
            f"  🌺 BEGONYA CONVEXITY │ CANLI SİNYAL: {sym} ({direction})",
            sep,
            f"DURUM           : {status}",
            f"MAKRO REJİM     : {macro_regime}",
            f"MAKRO KAPI      : {macro_bias} ({macro_status})",
            f"TEKNİK TREND    : {regime}",
            thin,
            f"Giriş Seviyesi  : {entry:.4f}",
            f"Stop Loss       : {sl:.4f} (1.5x ATR - Gürültüye Dayanıklı)",
            f"%50 Kâr Al (2R) : {tp2:.4f} (+1.0 R Kasaya Kilitlenecek)",
            f"Kalan %50 Takip : 2.0x ATR Trailing Stop (Kârı Koştur)",
            thin,
            f"Dinamik Risk    : ${actual_risk_dollars:.2f} (%{risk_pct:.1f} Temel Risk @ 1R)",
            f"Aksiyon         : {action_text}",
            sep
        ]
    else:
        entry = item["entry_price"]
        radar_dir = item.get("radar_direction", "RADAR")
        lines = [
            sep,
            f"  👀 BEGONYA CONVEXITY │ RADAR / TAKİP LİSTESİ: {sym} ({radar_dir})",
            sep,
            f"DURUM           : {status}",
            f"MAKRO REJİM     : {macro_regime}",
            f"MAKRO KAPI      : {macro_bias} ({macro_status})",
            f"TEKNİK TREND    : {regime}",
            f"Anlık Fiyat     : {curr:.4f}",
            f"Kritik Kırılım  : {entry:.4f}",
            f"Aksiyon         : Kırılım henüz tetiklenmedi. Günlük kapanışı ve makro kapıyı izle.",
            sep
        ]
    return "\n".join(lines)

def run_live_cycle():
    cfg_path = PROJECT_DIR / "config" / "convexity_config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    loader = MarketLoader()
    detector = TrendDetector(config)
    scanner = LiveConvexityScanner(config, loader, detector)
    rot_engine = RotationalSelectionEngine(config)
    telemetry = TelemetryLogger()

    initial_capital = config.get("backtest", {}).get("initial_capital", 10000.0)
    risk_pct = config.get("execution", {}).get("risk_per_trade_pct", 2.0)
    risk_dollars_1r = initial_capital * (risk_pct / 100.0)

    print("\n" + "═" * 80)
    print(" 🌺 BEGONYA CONVEXITY ENGINE │ CANLI ROTASYON & PİYASA RADARI 🌺 ")
    print(f"   • Risk Modeli       : %{risk_pct:.1f} ($ {risk_dollars_1r:,.2f} / 1R @ $10,000 Hesap)")
    print("   • Evren             : 16 Küresel Varlık (Genişletilmiş 'Olabildiğince Çok' Modeli)")
    print("   • Çıkarma Protokolü : 200 EMA Kırılımı + Volatilite Sıkışması + Karantina")
    print("═" * 80 + "\n")

    # 1. Piyasa Verilerini Yükle
    symbols = config.get("backtest", {}).get("symbols", [])
    market_data = {}
    for sym in symbols:
        df = loader.load_symbol_data(sym, period="1y")
        if not df.empty and len(df) >= 50:
            market_data[sym] = df

    # 2. Dinamik Rotasyon ve Listeden Çıkarma Denetimi
    today = datetime.now()
    active_symbols, audit_events = rot_engine.evaluate_and_rebalance(
        current_date=today,
        symbol_data_map=market_data,
        open_positions=set()
    )

    evicted_symbols = [a for a in audit_events if a.action == "EVICTED"]

    print(f"🟢 ŞU AN AKTİF LİSTEDE OLANLAR ({len(active_symbols)} Varlık):")
    print("   " + ", ".join(sorted(list(active_symbols))) if active_symbols else "   (Aktif varlık yok)")

    if evicted_symbols:
        print(f"\n🔴 BUGÜN KIRMIZI KARTLA ÇIKARILANLAR ({len(evicted_symbols)} Varlık):")
        for ev in evicted_symbols:
            print(f"   • {ev.symbol:<8}: {ev.reason}")

    # 3. Kırılım ve Radar Taraması
    signals = scanner.scan_all_symbols()

    # Yalnızca aktif listede olan sinyalleri filtrele
    valid_fresh_breaks = [s for s in signals if s["type"] == "FRESH_BREAKOUT" and s["symbol"] in active_symbols]
    radars = [s for s in signals if s["type"] == "WATCHLIST_RADAR" and s["symbol"] in active_symbols]

    signals_dir = PROJECT_DIR / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    with open(signals_dir / "active_signals.json", "w", encoding="utf-8") as f:
        json.dump(signals, f, indent=2, ensure_ascii=False)

    if valid_fresh_breaks:
        print(f"\n🚨 YENİ İŞLEM SİNYALİ VERENLER ({len(valid_fresh_breaks)} adet):")
        for fb in valid_fresh_breaks:
            print(format_begonya_card(fb, capital=initial_capital, risk_pct=risk_pct) + "\n")
    else:
        print("\nℹ️ Bugün aktif listedeki varlıklardan taze kırılım sinyali veren yok.")

    if radars:
        print(f"\n👀 RADARA GİREN / KIRILIMA YAKINLAŞANLAR ({len(radars)} adet):")
        for r in radars:
            print(format_begonya_card(r, capital=initial_capital, risk_pct=risk_pct) + "\n")

    # 4. TELEGRAM BİLDİRİMİ GÖNDER (@Begonyamatematiksel_bot)
    try:
        active_list_str = ", ".join(sorted(list(active_symbols)))
        evicted_str = "\n".join([f"• <b>{e.symbol}</b>: {e.reason}" for e in evicted_symbols]) if evicted_symbols else "Yok (Tüm aktifler sağlıklı)"
        
        signal_status_str = ""
        if valid_fresh_breaks:
            signal_status_str = f"🚨 <b>BUGÜN YENİ İŞLEM:</b> {len(valid_fresh_breaks)} adet kırılım var!\n"
            for fb in valid_fresh_breaks:
                signal_status_str += f"➔ <b>{fb['direction']} {fb['symbol']}</b> @ {fb['entry_price']:.4f} (SL: {fb['stop_loss']:.4f})\n"
        else:
            signal_status_str = "ℹ️ Bugün taze kırılım yok (Mevcut trendler izleniyor)."

        radar_str = ""
        if radars:
            radar_str = "\n👀 <b>Radardakiler:</b> " + ", ".join([f"{r['symbol']} ({r.get('radar_direction','RADAR')})" for r in radars])

        tg_msg = (
            f"🌺 <b>BEGONYA CONVEXITY ENGINE │ CANLI DURUM RAPORU</b> 🌺\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>Sistem Durumu:</b> CANLI AKTİF (LIVE)\n"
            f"🛡️ <b>Risk Modeli:</b> %{risk_pct:.1f} (${risk_dollars_1r:,.2f} / 1R)\n"
            f"⏰ <b>Zaman:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 <b>Aktif İşlem Listesi ({len(active_symbols)}):</b>\n<code>{active_list_str}</code>\n\n"
            f"🔴 <b>Kırmızı Kart / Kovulanlar:</b>\n{evicted_str}\n\n"
            f"{signal_status_str}"
            f"{radar_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 <i>Begonya Matematiksel Bot devrede. Otomatik infaz ve koruma aktif.</i>"
        )
        telemetry.send_custom_message(tg_msg)
        print("\n📲 Telegram (@Begonyamatematiksel_bot) canlı durum bildirimi başarıyla iletildi!")
    except Exception as e:
        print(f"\n⚠️ Telegram bildirimi gönderilemedi: {e}")

    print("✅ Canlı tarama ve yaşam döngüsü başarıyla tamamlandı.\n")

if __name__ == "__main__":
    run_live_cycle()
