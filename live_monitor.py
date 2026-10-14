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
    """Saf Matematiksel Bot formatında sinyal/takip kartı oluşturur."""
    sym = item["symbol"]
    direction = item["direction"]
    status = item["status"]
    regime = item["regime"]
    curr = item["current_price"]
    actual_risk_dollars = round(capital * (risk_pct / 100.0), 2)

    sep = "═" * 70
    thin = "─" * 70

    if item["type"] == "FRESH_BREAKOUT":
        entry = item["entry_price"]
        sl = item["stop_loss"]
        tp2 = item["partial_tp_2r"]

        lines = [
            sep,
            f"  🤖 MATEMATİKSEL BOT │ CANLI SİNYAL: {sym} ({direction})",
            sep,
            f"DURUM           : {status}",
            f"MODEL           : Saf Matematiksel Rotasyon (Bağımsız)",
            f"TEKNİK TREND    : {regime}",
            thin,
            f"Giriş Seviyesi  : {entry:.4f}",
            f"Stop Loss       : {sl:.4f} (1.5x ATR - Gürültüye Dayanıklı)",
            f"%50 Kâr Al (2R) : {tp2:.4f} (+1.0 R Kasaya Kilitlenecek)",
            f"Kalan %50 Takip : 2.0x ATR Trailing Stop (Kârı Koştur)",
            thin,
            f"Sabit Risk (1R) : ${actual_risk_dollars:.2f} (%{risk_pct:.1f} Bakiye Riski)",
            f"Aksiyon         : ✅ Matematiksel Kırılım Onaylandı. İki bilet emrini ilet.",
            sep
        ]
    else:
        entry = item["entry_price"]
        radar_dir = item.get("radar_direction", "RADAR")
        lines = [
            sep,
            f"  👀 MATEMATİKSEL BOT │ RADAR / TAKİP LİSTESİ: {sym} ({radar_dir})",
            sep,
            f"DURUM           : {status}",
            f"TEKNİK TREND    : {regime}",
            f"Anlık Fiyat     : {curr:.4f}",
            f"Kritik Kırılım  : {entry:.4f}",
            f"Aksiyon         : Kırılım henüz tetiklenmedi. Günlük kapanışı izle.",
            sep
        ]
    return "\n".join(lines)

def get_passive_reasons(symbols: list, active_symbols: set, market_data: dict, rot_engine: RotationalSelectionEngine) -> list:
    """Aktif listede olmayan varlıkların matematiksel elenme/kırmızı kart nedenlerini döndürür."""
    reasons = []
    for sym in sorted(symbols):
        if sym in active_symbols:
            continue
        df = market_data.get(sym)
        if df is None or df.empty:
            reasons.append((sym, "Veri yetersiz"))
            continue
        row = df.iloc[-1]
        close = float(row["close"])
        ema200 = float(row.get("ema200", close))
        atr = float(row.get("atr14", 0.0))
        atr_pct = (atr / close) * 100.0 if close > 0 else 0.0
        if close <= ema200:
            reasons.append((sym, f"200 EMA Altı ({close:.4f} | EMA: {ema200:.4f})"))
        elif atr_pct < rot_engine.min_atr_pct:
            reasons.append((sym, f"Volatilite Sıkışması (ATR %{atr_pct:.2f} / Min %{rot_engine.min_atr_pct})"))
        else:
            reasons.append((sym, "30 Günlük Karantina / Sıralama Dışı"))
    return reasons

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
    print(" 🤖 MATEMATİKSEL BOT (CONVEXITY ENGINE) │ BAĞIMSIZ CANLI PİYASA RADARI 🤖 ")
    print(f"   • Çalışma Modu      : %100 Bağımsız & Saf Matematiksel (Makro Veto Yok)")
    print(f"   • Risk Modeli       : %{risk_pct:.1f} ($ {risk_dollars_1r:,.2f} / 1R @ $10,000 Baz)")
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

    passive_reasons = get_passive_reasons(symbols, active_symbols, market_data, rot_engine)

    print(f"🟢 ŞU AN AKTİF LİSTEDE OLANLAR ({len(active_symbols)} Varlık):")
    print("   " + ", ".join(sorted(list(active_symbols))) if active_symbols else "   (Aktif varlık yok)")

    if passive_reasons:
        print(f"\n🔴 KIRMIZI KARTLI / PASİF LİSTEDEKİLER ({len(passive_reasons)} Varlık):")
        for sym, r_text in passive_reasons:
            print(f"   • {sym:<8}: {r_text}")

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
        passive_str = "\n".join([f"• <b>{s}</b>: {r}" for s, r in passive_reasons]) if passive_reasons else "Yok"
        
        signal_status_str = ""
        if valid_fresh_breaks:
            signal_status_str = f"🚨 <b>BUGÜN YENİ İŞLEM ({len(valid_fresh_breaks)}):</b>\n"
            for fb in valid_fresh_breaks:
                signal_status_str += f"➔ <b>{fb['direction']} {fb['symbol']}</b> @ {fb['entry_price']:.4f} (SL: {fb['stop_loss']:.4f})\n"
        else:
            signal_status_str = "ℹ️ Bugün taze kırılım yok (Mevcut trendler izleniyor)."

        radar_str = ""
        if radars:
            radar_str = "\n👀 <b>Radardakiler:</b> " + ", ".join([f"{r['symbol']} ({r.get('radar_direction','RADAR')})" for r in radars])

        tg_msg = (
            f"🤖 <b>MATEMATİKSEL BOT │ BAĞIMSIZ CANLI DURUM RAPORU</b> 🤖\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>Mod:</b> %100 BAĞIMSIZ & SAF MATEMATİKSEL\n"
            f"🛡️ <b>Risk Modeli:</b> %{risk_pct:.1f} (Sabit 1R)\n"
            f"⏰ <b>Zaman:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 <b>Aktif İşlem Listesi ({len(active_symbols)}):</b>\n<code>{active_list_str}</code>\n\n"
            f"🔴 <b>Kırmızı Kart / Pasif ({len(passive_reasons)}):</b>\n{passive_str}\n\n"
            f"{signal_status_str}"
            f"{radar_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <i>Bağımsız Matematiksel Bot 7/24 nöbette.</i>"
        )
        telemetry.send_custom_message(tg_msg)
        print("\n📲 Telegram (@Begonyamatematiksel_bot) bağımsız durum bildirimi başarıyla iletildi!")
    except Exception as e:
        print(f"\n⚠️ Telegram bildirimi gönderilemedi: {e}")

    print("✅ Canlı tarama ve yaşam döngüsü başarıyla tamamlandı.\n")

if __name__ == "__main__":
    run_live_cycle()
