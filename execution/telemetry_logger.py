import os
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("TelemetryLogger")

class TelemetryLogger:
    """
    Tüm işlem infazlarını, istenen vs gerçekleşen fiyatları, slippage'ı
    ve pozisyon kapanışlarını loglar.
    Begonya CLT Telegram botu üzerinden 'Matematiksel Bot' başlığıyla bildirim fırlatır.
    """

    def __init__(self, log_dir: Optional[Path] = None, telegram_token: str = "", telegram_chat_id: str = ""):
        self.log_dir = log_dir or Path(__file__).resolve().parent.parent / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.audit_file = self.log_dir / "execution_audit.jsonl"
        
        # .env dosyasını oku (varsa)
        self._load_env_file()

        # Token ve Chat ID yükle (Sırasıyla: argüman -> env -> config)
        self.telegram_token = (
            telegram_token
            or os.environ.get("TELEGRAM_BOT_TOKEN")
            or self._load_cfg_token()
        )
        self.telegram_chat_id = (
            telegram_chat_id
            or os.environ.get("TELEGRAM_CHAT_ID")
            or self._load_cfg_chat_id()
        )
        self.header_title = "Matematiksel Bot"

    def _load_env_file(self):
        """Proje kökündeki .env dosyasını okuyup os.environ'a aktarır."""
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.exists():
            try:
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip()
                            if k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass

    def _load_cfg_token(self) -> str:
        try:
            cfg_path = Path(__file__).resolve().parent.parent / "config" / "convexity_config.json"
            if cfg_path.exists():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    token = cfg.get("telegram", {}).get("bot_token", "")
                    if token and "YOUR_" not in token:
                        return token
        except Exception:
            pass
        return ""

    def _load_cfg_chat_id(self) -> str:
        try:
            cfg_path = Path(__file__).resolve().parent.parent / "config" / "convexity_config.json"
            if cfg_path.exists():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    cid = str(cfg.get("telegram", {}).get("chat_id", ""))
                    if cid and "YOUR_" not in cid:
                        return cid
        except Exception:
            pass
        return ""

    def log_execution(self, event_data: Dict[str, Any]):
        """Olayı JSONL dosyasına kaydeder ve Telegram'a bildirir."""
        event_data["timestamp"] = datetime.now().isoformat()
        
        # 1. Dosyaya yaz
        try:
            with open(self.audit_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event_data, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Audit log yazılamadı: {e}")

        # 2. Konsola bas
        self._print_console(event_data)

        # 3. Telegram'a gönder
        if self.telegram_token and self.telegram_chat_id:
            self._send_telegram(event_data)

    def _print_console(self, d: Dict[str, Any]):
        status = d.get("status", "INFO")
        sym = d.get("symbol", "")
        action = d.get("action", "")
        lot_a = d.get("lot_a", d.get("lot", 0.0))
        lot_b = d.get("lot_b", 0.0)
        req_p = d.get("requested_price", 0.0)
        fill_p = d.get("avg_fill_price", d.get("fill_price", 0.0))
        slip_pts = d.get("slippage_pts", 0.0)
        slip_r = d.get("slippage_r", 0.0)

        print(f"\n📡 [{self.header_title} | {status}] {action} {sym} (A:{lot_a}L, B:{lot_b}L) | İstenen: {req_p:.4f} ➔ Gerçekleşen: {fill_p:.4f} | Slippage: {slip_pts:+.2f} pts ({slip_r:+.3f}R)")

    def _send_telegram(self, d: Dict[str, Any]):
        try:
            status = d.get("status", "INFO")
            action = d.get("action", "")
            sym = d.get("symbol", "")

            # SADECE ve SADECE Gerçekten İnfaz Edilen İşlemlerde Telegram Mesajı Gönder
            if status == "FILLED":
                icon = "🟢" if "BUY" in action.upper() or "LONG" in action.upper() else "🔴"
                msg = (
                    f"🤖 <b>{self.header_title}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"{icon} <b>YENİ İŞLEM AÇILDI: {action} {sym}</b>\n\n"
                    f"🎫 <b>Bilet A (Hedefli):</b> {d.get('lot_a')} Lot ➔ TP: {d.get('tp_a'):.5f} (+2.0R)\n"
                    f"🎫 <b>Bilet B (Koşucu):</b> {d.get('lot_b')} Lot ➔ TP: Sonsuz (2.5x ATR Trailing)\n"
                    f"🛑 <b>Ortak Stop Loss:</b> {d.get('sl'):.5f}\n"
                    f"🎯 <b>Ortalama Giriş:</b> {d.get('avg_fill_price', 0):.5f}\n"
                    f"⚡ <b>Slippage:</b> {d.get('slippage_pts', 0):+.2f} pts ({d.get('slippage_r', 0):+.3f}R)\n"
                    f"📊 <b>Anlık Spread:</b> {d.get('spread', 0)} pts\n"
                    f"⏰ <b>Zaman:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
            elif status in ("PARTIAL_OR_FAILED", "REJECTED_SPREAD", "REJECTED_PRICE_MISMATCH", "ERROR", "FAILED"):
                # Açılmayan / reddedilen işlemler Telegram'a ATILMAZ (Kullanıcı bildirimi istemiyor).
                # Yalnızca konsola ve execution_audit.jsonl dosyasına loglanır.
                return

            # B) Trailing Stop Güncelleme Bildirimi
            elif action == "TRAILING_UPDATE":
                msg = (
                    f"🤖 <b>{self.header_title}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"🛡️ <b>TRAILING STOP GÜNCELLENDİ: {sym}</b>\n\n"
                    f"🎫 <b>Bilet No:</b> #{d.get('ticket')}\n"
                    f"📉 <b>Eski Stop:</b> {d.get('old_sl'):.5f}\n"
                    f"📈 <b>Yeni Stop:</b> {d.get('new_sl'):.5f}\n"
                    f"✅ <b>Kural:</b> 2.5x ATR Kâr Kilitleme\n"
                    f"⏰ <b>Zaman:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )

            # C) Pozisyon Kapanış Bildirimi
            elif "CLOSE" in action.upper() or status == "CLOSED":
                pnl_r = d.get("pnl_r", 0.0)
                pnl_usd = d.get("pnl_usd", 0.0)
                res_icon = "💰" if pnl_r > 0 else "🛑"
                msg = (
                    f"🤖 <b>{self.header_title}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"{res_icon} <b>İŞLEM KAPANDI: {sym}</b>\n\n"
                    f"🎫 <b>Bilet:</b> #{d.get('ticket', 'N/A')}\n"
                    f"📌 <b>Neden:</b> {d.get('reason', 'Kapanış')}\n"
                    f"💵 <b>Net PnL (R):</b> {pnl_r:+.2f} R\n"
                    f"💲 <b>Net PnL ($):</b> ${pnl_usd:+.2f}\n"
                    f"⏰ <b>Zaman:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )

            # D) Diğer Durumlar (Red, Hata vb.)
            else:
                msg = (
                    f"🤖 <b>{self.header_title}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ <b>DURUM: {status}</b>\n"
                    f"<b>Varlık:</b> {sym}\n"
                    f"<b>Açıklama:</b> {d.get('reason', 'Detay yok')}\n"
                    f"⏰ <b>Zaman:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )

            self.send_custom_message(msg)
        except Exception as e:
            logger.warning(f"Telegram telemetry iletilemedi: {e}")

    def send_custom_message(self, html_text: str):
        """Doğrudan formatlı HTML mesajı gönderir."""
        if not self.telegram_token or not self.telegram_chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": html_text,
                "parse_mode": "HTML"
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
        except Exception as e:
            logger.warning(f"Telegram mesaj iletim hatası: {e}")
