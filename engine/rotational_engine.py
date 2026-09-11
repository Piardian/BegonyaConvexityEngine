import os
import sys
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Set
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

logger = logging.getLogger("RotationalEngine")

class EvictionReason(str, Enum):
    RANK_DECAY = "RS Sıralaması 7. Sıranın Altına Düştü (Momentum Kaybı)"
    EMA200_BREAKDOWN = "Fiyat 200 EMA Altına İndi (Trend Ölümü)"
    VOLATILITY_CHOP = "ATR Volatilitesi Sıkıştı (< %0.8 Testere Tehlikesi)"
    CIRCUIT_BREAKER_LOSS = "Son 30 Günde Peş Peşe 2 Stop (Karantina)"

@dataclass
class AuditRecord:
    date: str
    symbol: str
    action: str          # "ADMITTED", "EVICTED", "QUARANTINE_ENTER", "QUARANTINE_EXIT"
    reason: str
    rank: int
    rs_score: float
    current_price: float
    ema200: float
    atr_pct: float

class RotationalSelectionEngine:
    """
    Begonya Dinamik Rotasyon ve Listeden Çıkarma (Selection & Eviction) Motoru.
    
    Temel Prensipler:
    1. Haftalık Göreceli Güç (RS) Sıralaması: 16 varlık taranır, son 60 günlük momentum hesaplanır.
    2. Kalite Filtreleri: Fiyat > 200 EMA olmak zorunda (Makro Boğa).
    3. Histerezis (Giriş/Çıkış Tamponu):
       - İlk 4'e giren (Rank <= 4) listeye ALINIR.
       - 7. sıranın altına düşmeyen (Rank <= 6) listede TUTULUR.
       - 7. sıranın altına düşen (Rank >= 7) kırmızı kartla LİSTEDEN ATILIR.
    4. Devre Kesici (Karantina): Son 30 günde peş peşe 2 stop veren varlık 30 gün kilitlenir.
    5. Zarif Tasfiye (Graceful Exit): Listeden atılan varlıkta açık pozisyon varsa apar topar kapatılmaz;
       yeni emirler dondurulur ve stop 1.0x ATR dar trailinge çekilir.
    """
    def __init__(self, config: dict):
        self.config = config
        rot_cfg = config.get("rotational_selection", {})
        self.max_active_universe = rot_cfg.get("max_active_universe", 4)
        self.admission_rank_threshold = rot_cfg.get("admission_rank_threshold", 4)
        self.eviction_rank_threshold = rot_cfg.get("eviction_rank_threshold", 7)
        
        evict_rules = rot_cfg.get("eviction_rules", {})
        self.require_above_ema200 = evict_rules.get("require_above_ema200", True)
        self.min_atr_pct = evict_rules.get("min_atr_pct", 0.80)  # %0.8
        self.max_consecutive_losses = evict_rules.get("max_consecutive_losses", 2)
        self.quarantine_days = evict_rules.get("quarantine_days", 30)

        # Durum takibi
        self.active_universe: Set[str] = set()
        self.quarantine_list: Dict[str, datetime] = {}  # symbol -> quarantine_until
        self.audit_log: List[AuditRecord] = []
        self.loss_history: Dict[str, List[datetime]] = {}  # symbol -> list of loss dates

    def record_trade_loss(self, symbol: str, loss_date: datetime):
        """İşlem zarar ettiğinde kaydeder, peş peşe 2 zarar varsa karantinaya alır."""
        if symbol not in self.loss_history:
            self.loss_history[symbol] = []
        
        self.loss_history[symbol].append(loss_date)
        # Son 30 gündeki zararları filtrele
        recent_losses = [d for d in self.loss_history[symbol] if (loss_date - d).days <= 30]
        self.loss_history[symbol] = recent_losses

        if len(recent_losses) >= self.max_consecutive_losses:
            quarantine_until = loss_date + timedelta(days=self.quarantine_days)
            self.quarantine_list[symbol] = quarantine_until
            
            # Eğer aktif listedeyse derhal listeden at
            if symbol in self.active_universe:
                self.active_universe.remove(symbol)
                self.audit_log.append(AuditRecord(
                    date=loss_date.strftime("%Y-%m-%d"),
                    symbol=symbol,
                    action="EVICTED",
                    reason=EvictionReason.CIRCUIT_BREAKER_LOSS.value,
                    rank=99,
                    rs_score=-99.0,
                    current_price=0.0,
                    ema200=0.0,
                    atr_pct=0.0
                ))

    def evaluate_and_rebalance(
        self, 
        current_date: datetime, 
        symbol_data_map: Dict[str, pd.DataFrame],
        open_positions: Set[str]
    ) -> Tuple[Set[str], List[AuditRecord]]:
        """
        Haftalık rebalance fonksiyonu.
        Tüm evreni puanlar, histerezis uygulayarak listeye alımları ve çıkarmaları belirler.
        """
        date_str = current_date.strftime("%Y-%m-%d")
        new_events: List[AuditRecord] = []

        # 1. Karantina süresi dolanları temizle
        expired_quarantines = [s for s, q_until in self.quarantine_list.items() if current_date >= q_until]
        for s in expired_quarantines:
            del self.quarantine_list[s]
            record = AuditRecord(
                date=date_str,
                symbol=s,
                action="QUARANTINE_EXIT",
                reason="30 Günlük Karantina Süresi Doldu (Yeniden Aday)",
                rank=0,
                rs_score=0.0,
                current_price=0.0,
                ema200=0.0,
                atr_pct=0.0
            )
            self.audit_log.append(record)
            new_events.append(record)

        # 2. Varlıkları puanla
        candidate_scores = []

        for sym, df in symbol_data_map.items():
            # Timezone uyumluluğu sağla
            target_dt = current_date
            if hasattr(df.index, "tz") and df.index.tz is not None:
                if getattr(target_dt, "tzinfo", None) is None:
                    target_dt = pd.Timestamp(current_date, tz="UTC")
            else:
                if getattr(target_dt, "tzinfo", None) is not None:
                    target_dt = pd.Timestamp(current_date).tz_localize(None)

            if target_dt not in df.index:
                # O tarihte bar yoksa en yakın önceki barı bul
                sub_df = df[df.index <= target_dt]
                if sub_df.empty:
                    continue
                bar = sub_df.iloc[-1]
                bar_idx = len(sub_df) - 1
            else:
                bar_idx = df.index.get_loc(target_dt)
                bar = df.iloc[bar_idx]

            if bar_idx < 60:
                continue

            close = float(bar["close"])
            ema200 = float(bar.get("ema200", close))
            atr14 = float(bar.get("atr14", 0.0))
            atr_pct = (atr14 / close) * 100.0 if close > 0 else 0.0

            # 60 günlük Relative Strength (RS)
            close_60_ago = float(df.iloc[bar_idx - 60]["close"])
            rs_score = ((close - close_60_ago) / close_60_ago) * 100.0

            # Kriter kontrolleri
            is_above_ema200 = close >= ema200
            has_volatility = atr_pct >= self.min_atr_pct
            is_quarantined = sym in self.quarantine_list

            candidate_scores.append({
                "symbol": sym,
                "rs_score": rs_score,
                "close": close,
                "ema200": ema200,
                "atr_pct": atr_pct,
                "is_above_ema200": is_above_ema200,
                "has_volatility": has_volatility,
                "is_quarantined": is_quarantined
            })

        # Sadece 200 EMA üstünde ve karantinada olmayanları geçerli kabul et
        valid_candidates = [
            c for c in candidate_scores 
            if c["is_above_ema200"] and c["has_volatility"] and not c["is_quarantined"]
        ]
        # RS skoruna göre büyükten küçüğe sırala
        valid_candidates.sort(key=lambda x: x["rs_score"], reverse=True)

        # Sıralama haritası (Rank Map)
        rank_map = {c["symbol"]: idx + 1 for idx, c in enumerate(valid_candidates)}
        cand_data_map = {c["symbol"]: c for c in candidate_scores}

        # 3. LİSTEDEN ÇIKARMA (EVICTION) KONTROLÜ
        # Şu an aktif listede olan varlıklar incelenir
        symbols_to_evict = []
        for active_sym in list(self.active_universe):
            c_info = cand_data_map.get(active_sym)
            if not c_info:
                continue

            eviction_reason = None

            # Kural A: 200 EMA Kırıldı mı? (Trend Ölümü)
            if not c_info["is_above_ema200"]:
                eviction_reason = EvictionReason.EMA200_BREAKDOWN.value
            
            # Kural B: Karantinaya girdi mi?
            elif c_info["is_quarantined"]:
                eviction_reason = EvictionReason.CIRCUIT_BREAKER_LOSS.value

            # Kural C: Volatilite söndü mü? (Chop)
            elif not c_info["has_volatility"]:
                eviction_reason = EvictionReason.VOLATILITY_CHOP.value

            # Kural D: Sıralama 7. sıranın altına düştü mü? (Histerezis Kuralı)
            else:
                current_rank = rank_map.get(active_sym, 99)
                if current_rank >= self.eviction_rank_threshold:
                    eviction_reason = f"{EvictionReason.RANK_DECAY.value} (Yeni Sıra: #{current_rank})"

            if eviction_reason:
                symbols_to_evict.append((active_sym, eviction_reason, c_info, rank_map.get(active_sym, 99)))

        for sym, reason, c_info, rk in symbols_to_evict:
            self.active_universe.remove(sym)
            record = AuditRecord(
                date=date_str,
                symbol=sym,
                action="EVICTED",
                reason=reason,
                rank=rk,
                rs_score=round(c_info["rs_score"], 2),
                current_price=round(c_info["close"], 4),
                ema200=round(c_info["ema200"], 4),
                atr_pct=round(c_info["atr_pct"], 2)
            )
            self.audit_log.append(record)
            new_events.append(record)

        # 4. LİSTEYE ALIM (ADMISSION) KONTROLÜ
        # Top-4 adayları listeye dahil et
        for c in valid_candidates:
            sym = c["symbol"]
            rk = rank_map[sym]

            # Eğer kontenjan dolduysa dur
            if len(self.active_universe) >= self.max_active_universe:
                break

            # Sadece Top-4'e girenler içeri alınır
            if rk <= self.admission_rank_threshold:
                if sym not in self.active_universe:
                    self.active_universe.add(sym)
                    record = AuditRecord(
                        date=date_str,
                        symbol=sym,
                        action="ADMITTED",
                        reason=f"Top-4 Göreceli Güç (RS #{rk} - Momentum %{c['rs_score']:.1f})",
                        rank=rk,
                        rs_score=round(c["rs_score"], 2),
                        current_price=round(c["close"], 4),
                        ema200=round(c["ema200"], 4),
                        atr_pct=round(c["atr_pct"], 2)
                    )
                    self.audit_log.append(record)
                    new_events.append(record)

        return self.active_universe, new_events
