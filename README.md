# 🌺 BEGONYA CONVEXITY ENGINE
### Asymmetric Risk D1/W1 Convexity Engine & Automated MT5 Trend-Following Architecture

[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5-green.svg)](https://www.metatrader5.com/)
[![Strategy](https://img.shields.io/badge/Strategy-Convexity%20Trend--Following-blueviolet.svg)]()
[![Timeframe](https://img.shields.io/badge/Timeframe-Daily%20(D1)-orange.svg)]()
[![License](https://img.shields.io/badge/License-Proprietary-red.svg)]()

**Begonya Convexity Engine**, perakende gün içi piyasa gürültüsünü (noise), makas aralığı (spread) ve komisyon erozyonunu tamamen bertaraf eden; **Asimetrik Risk Yönetimi, Parçalı Kâr Alma (Scaling Out @ 2.0R), Trailing Runner ve 200 EMA Makro Rejim Kalkanı** üzerine kurulu kurumsal düzeyde bir algoritmik trend takip ve infaz sistemidir.

---

## 📑 İçindekiler
- [1. Sistemin Temel Felsefesi & Konveksite](#-1-sistemin-temel-felsefesi--konveksite)
- [2. Matematiksel & Algoritmik Model](#-2-matematiksel--algoritmik-model)
  - [A. Lookahead-Free Donchian Kırılımı](#a-lookahead-free-donchian-k%C4%B1r%C4%B1l%C4%B1m%C4%B1)
  - [B. 200 EMA Makro Rejim Kalkanı](#b-200-ema-makro-rejim-kalkan%C4%B1)
  - [C. Volatilite Stopu (1.5x ATR)](#c-volatilite-stopu-15x-atr)
  - [D. İki Biletli (Split-Ticket) İnfaz Mimarisi](#d-iki-biletli-split-ticket-%C4%B0nfaz-mimarisi)
- [3. Sistem Mimarisi](#-3-sistem-mimarisi)
- [4. 16 Küresel Varlık Evreni](#-4-16-k%C3%BCresel-varl%C4%B1k-evreni)
- [5. MetaTrader 5 (MT5) İnfaz & Yaşam Döngüsü](#-5-metatrader-5-mt5-%C4%B0nfaz--ya%C5%9Fam-d%C3%B6ng%C3%BCs%C3%BC)
- [6. Makro Kapı Entegrasyonu (Begonya Macro Gate)](#-6-makro-kap%C4%B1-entegrasyonu-begonya-macro-gate)
- [7. Dizin Yapısı](#-7-dizin-yap%C4%B1s%C4%B1)
- [8. Kurulum ve Başlangıç](#-8-kurulum-ve-ba%C5%9Flang%C4%B1%C3%A7)
- [9. Çalıştırma Rehberi](#-9-%C3%87al%C4%B1%C5%9Ft%C4%B1rma-rehberi)
- [10. Telemetri ve Bildirim Sistemi](#-10-telemetri-ve-bildirim-sistemi)
- [11. Güvenlik ve Risk Feragatnamesi](#-11-g%C3%BCvenlik-ve-risk-feragatnamesi)

---

## 🏛️ 1. Sistemin Temel Felsefesi & Konveksite

Geleneksel perakende yaklaşımların %90'dan fazlası düşük zaman dilimlerinde (M5-M15) aşırı işlem (overtrading), kayma (slippage) ve komisyon maliyetleri nedeniyle sermaye erimesine uğrar. **Begonya Convexity Engine**, Nassim Nicholas Taleb ve Richard Dennis (Turtle Traders) ekolünden ilham alan matematiksel **Konveksite (Convexity / Positive Skewness)** ilkesini uygular:

\text{Beklenen Değer } (EV) = (P_{\text{win}} \times R_{\text{win}}) - (P_{\text{loss}} \times R_{\text{loss}})

- **Sınırlı & Sabit Risk:** Her işlemde hesap büyüklüğünün azami sabit **%0.5 - %2.0'si** riske edilir (1R).
- **Asimetrik Getiri:** Zararlar erken kesilirken (en fazla $-1.0\text{R}$), kârlar serbest bırakılır ($+2\text{R}, +5\text{R}, +10\text{R}+$ fat-tail trendler).
- **Psikolojik & Finansal Zırh:** Pozisyon $+2.0\text{R}$ gördüğünde yarısı realize edilerek **$+1.0\text{R}$ net kâr cebe kilitlenir**. Kalan yarının stopu başabaşa taşınarak işlem **sıfır riskli "ücretsiz piyango biletine"** dönüştürülür.

`
       Getiri (PnL)
           ▲
           │                                 / (Bilet B: Trailing Runner 8R+)
           │                                /
    +2.0R ─┼───────────────●───────────────/
           │              /│
    +1.0R ─┼── Kilitlenen ─│─────────────── (Bilet A Net Realize: +1.0R)
           │     Kâr      │
       0.0 ┼──────────────┼─────────────── (Stop Maliyete Taşındı - Breakeven)
           │              │
    -1.0R ─┼──────■───────┴─────────────── Fiyat / Trend
           │   (İlk SL: 1.5x ATR)
           ▼
`

---

## 📐 2. Matematiksel & Algoritmik Model

### A. Lookahead-Free Donchian Kırılımı
Sistem, geleceği görme yanılsamasını (lookahead bias) matematiksel olarak imkânsız kılmak için daima -1$ barının kesinleşmiş kapanışını baz alır:
- **Üst Bant (20 Günlük Zirve):**
  H_{20, t} = \max\left(High_{t-20}, High_{t-19}, \dots, High_{t-1}\right)
- **Alt Bant (20 Günlük Dip):**
  L_{20, t} = \min\left(Low_{t-20}, Low_{t-19}, \dots, Low_{t-1}\right)

Kırılım koşulu:
\text{Long Sinyali } \iff Close_{t} > H_{20, t}
\text{Short Sinyali } \iff Close_{t} < L_{20, t}

### B. 200 EMA Makro Rejim Kalkanı
Karşı trend kırılımlarında testere piyasasına (whipsaw) yakalanmamak için kurumsal rejim filtresi devrededir:
\text{Rejim Kalkanı} = \begin{cases} 
\text{Geçerli Long}, & \text{eğer } Close_{t} > EMA_{200}(Close) \\
\text{Geçerli Short}, & \text{eğer } Close_{t} < EMA_{200}(Close) \\
\text{VETO (İptal)}, & \text{aksi takdirde}
\end{cases}

### C. Volatilite Stopu (1.5x ATR)
Gürültüye bağlı erken stoplanmaları önlemek için piyasanın dinamik volatilitesi ({14}$) kullanılır:
\text{Risk Mesafesi } (R_{\text{dist}}) = 1.5 \times ATR_{14}(t)
- **Long Stop-Loss:**  - R_{\text{dist}}$
- **Short Stop-Loss:**  + R_{\text{dist}}$

### D. İki Biletli (Split-Ticket) İnfaz Mimarisi
Her onaylı sinyal için MetaTrader 5 üzerinde aynı anda iki bağımsız emir açılır:

| Parametre | Bilet A (Hedefli Kâr - Scaler) | Bilet B (Koşucu - Trailing Runner) |
| :--- | :--- | :--- |
| **Magic Numarası** | 260901 | 260902 |
| **Hacim Oranı** | Toplam Pozisyonun **%50'si** | Toplam Pozisyonun **%50'si** |
| **İlk Stop Loss** |  \pm (1.5 \times ATR_{14})$ |  \pm (1.5 \times ATR_{14})$ |
| **Take Profit** | **Sabit $+2.0\text{R}$** ( \pm 2 \times R_{\text{dist}}$) | **YOK (Sonsuz)** |
| **Davranış** | $+2\text{R}$ vurulduğu an kapanır, $+1.0\text{R}$ nakit kilitler. | Bilet A kapandığında SL maliyete ($+0.05\text{R}$) çekilir. |
| **Sürme Kuralı** | Sabit Limit Emir | Zirve fiyattan .5 \times ATR_{14}$ mesafeli dinamik izleyen stop. |

---

## 🏗️ 3. Sistem Mimarisi

`mermaid
flowchart TD
    A[Yahoo Finance / Broker D1 Bar Akışı] --> B[Data Engine: MarketLoader]
    B --> C[Teknik İndikatörler: ATR14, Donchian20, EMA200]
    
    C --> D[Rotational Engine: 16 Varlık Evreni]
    D -->|Karantina / 200 EMA / Volatilite Filtresi| E[Aktif Radar Listesi]
    
    E --> F[Trend Detector: 20 Günlük Kırılım]
    F --> G{Sinyal Var mı?}
    G -- Evet --> H[Macro Gate Adapter: Begonya Rejim Denetimi]
    G -- Hayır --> Z[Bekleme / Radar Güncelleme]
    
    H -->|Makro Onayı Alındı| I[MT5 Execution Router]
    H -->|Kriz / Yön Veto| Y[İşlem İptal / Audit Log]
    
    I --> J[Lot & Contract Size Normalizasyonu]
    J --> K[Spread & Rollover Denetimi]
    
    K --> L[Split-Ticket İnfazı]
    L --> M[Bilet A: Magic 260901 | %50 Hacim | TP: +2.0R]
    L --> N[Bilet B: Magic 260902 | %50 Hacim | TP: Yok - Trailing SL]
    
    M & N --> O[Trade Lifecycle Daemon]
    O -->|Canlı Bar & Tick Takibi| P[Trailing Stop & Breakeven Güncelleyici]
    O -->|İnfaz / TP / SL Olayları| Q[Telemetry Logger]
    Q --> R[Telegram Botu: Matematiksel Bot]
    Q --> S[execution_audit.jsonl]
`

---

## 🌐 4. 16 Küresel Varlık Evreni

Sistem, küresel piyasalarda tam korelasyon dağıtımı (diversification) sağlayan 5 ana varlık sınıfında işlem yapar:

| Varlık Sınıfı | Semboller | Açıklama |
| :--- | :--- | :--- |
| **Forex Majörler** | EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF | Global likidite ve merkez bankası faiz döngüleri |
| **Forex Çaprazlar** | EURJPY, GBPJPY | Yüksek momentumlu carry-trade dalgaları |
| **Değerli Metaller** | XAUUSD (Altın), SILVER (Gümüş) | Enflasyon, jeopolitik risk ve parasal genişleme sığınağı |
| **Enerji** | CL_OIL (WTI Ham Petrol) | Küresel arz-talep ve sanayi döngüsü |
| **Hisse Endeksleri** | NAS100 (Nasdaq 100), SP500 (S&P 500) | Küresel teknoloji ve kurumsal kârlılık trendleri |
| **Kripto Varlıklar** | BTCUSD, ETHUSD, SOLUSD | Yüksek konveksite ve parabolik makro benimseme döngüleri |

---

## ⚡ 5. MetaTrader 5 (MT5) İnfaz & Yaşam Döngüsü

### 1. Dinamik Lot ve Sözleşme Boyutu Normalizasyonu
Her enstrümanın MT5 üzerindeki 	rade_tick_size, 	rade_tick_value, olume_min, olume_max ve olume_step değerleri sorgulanır. Hesap risk miktarına göre tam 1R değerindeki lot hesaplanıp Bilet A ve Bilet B için kusursuz biçimde bölünür.

### 2. Spread Spike & Rollover Filtresi
Günün rollover saatlerinde (23:45 - 00:30 UTC) veya ani haber şoklarında spread normalin .0\times$ katını aşarsa emir iletimi kilitlenir, sermaye korunur.

### 3. Kayma (Slippage) Denetimi
İstenen fiyat ile gerçekleşen fiyat arasındaki sapma hem pip cinsinden hem de $ sapması ($\Delta R$) olarak milisaniyelik telemetri günlüğüne kaydedilir.

---

## 🧠 6. Makro Kapı Entegrasyonu (Begonya Macro Gate)

Begonya Convexity Engine, ana **Begonya Makro Analitik Motoru** (shared/macro_bias_gate.json) ile çift yönlü iletişim kurar:
- **Kriz Modu / Sermaye Koruma (Veto):** Sistemik likidite krizlerinde motor tüm yeni girişleri otomatik olarak dondurur.
- **Yönsel Uyum (Directional Alignment):** Dolar Endeksi (DXY), 10 Yıllık ABD Tahvili (US10Y), Brent Petrol veya Bakır/Altın rasyosu teknik sinyali teyit etmiyorsa risk çarpanı düşürülür (0.5x) veya işlem veto edilir.
- **Kripto Ayrışması (BTC Decoupling):** Kripto paraların genel endekslerden ayrışma katsayısı izlenir.

---

## 📁 7. Dizin Yapısı

`
begonya_convexity_engine/
│
├── config/
│   └── convexity_config.json        # Strateji katsayıları, varlık listesi ve genel ayarlar
│
├── data/
│   └── market_loader.py             # D1 bar akışı, akıllı önbellek ve indikatör motoru
│
├── engine/
│   ├── trend_detector.py            # Donchian 20 + EMA 200 rejim tespit modülü
│   ├── rotational_engine.py         # 16 varlıklı dinamik rotasyon ve karantina motoru
│   ├── live_scanner.py              # Güncel piyasa tarayıcısı ve radar analizörü
│   └── convexity_simulator.py       # Çift biletli kâr alma ve trailing simülatörü
│
├── execution/
│   ├── mt5_execution_router.py      # MetaTrader 5 çift biletli infaz yöneticisi
│   ├── lifecycle_daemon.py          # 7/24 otonom MT5 yaşam döngüsü arka plan servisi
│   ├── symbol_mapper.py             # Broker sembol adı adaptörü (.pro, .raw, vb.)
│   ├── macro_gate_adapter.py        # Begonya makro rejim ve kriz vetosu köprüsü
│   ├── telemetry_logger.py          # Telegram ve JSONL telemetri kayıtçısı
│   └── begonya_signal_engine.py     # Harici sinyal entegrasyon arayüzü
│
├── analytics/
│   ├── performance_reporter.py      # Performans metrikleri ve ASCII getiri grafiği
│   ├── monte_carlo_engine.py        # 1.000 simülasyonlu Monte Carlo stres testi
│   ├── benchmark_collector.py       # Sinyal ve işlem kıyaslama kayıtçısı
│   └── test_*.py                    # Çoklu varlık ve geçmiş dönem doğrulama testleri
│
├── signals/
│   ├── active_signals.json          # Anlık taranmış canlı sinyaller ve radar
│   └── history/                     # Günlük arşivlenmiş sinyal kayıtları
│
├── state/
│   └── active_tickets.json          # MT5 üzerindeki canlı bilet durum hafızası
│
├── logs/
│   └── execution_audit.jsonl        # Gerçekleşen emirlerin milisaniyelik telemetri kaydı
│
├── live_monitor.py                  # Canlı piyasa tarama CLI arayüzü
├── run_convexity_backtest.py        # 5 yıllık kurumsal çoklu varlık backtest CLI arayüzü
├── requirements.txt                 # Python kütüphane bağımlılıkları
├── .env.example                     # Çevre değişkenleri ve gizli anahtar şablonu
└── README.md                        # Detaylı proje dokümantasyonu
`

---

## 🛠️ 8. Kurulum ve Başlangıç

### 1. Depoyu Klonlayın
```bash
git clone https://github.com/Piardian/BegonyaConvexityEngine.git
cd BegonyaConvexityEngine
```

### 2. Sanal Ortam Oluşturun ve Aktif Edin
`powershell
python -m venv venv
.\venv\Scripts\activate
`

### 3. Bağımlılıkları Yükleyin
`powershell
pip install -r requirements.txt
`

### 4. Çevre Değişkenlerini Yapılandırın
.env.example dosyasını .env olarak kopyalayıp Telegram bilgilerinizi tanımlayın:
`powershell
cp .env.example .env
`
.env içeriği:
`ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
`

---

## 🚀 9. Çalıştırma Rehberi

### A) 5 Yıllık Çoklu Varlık Backtesti (2021 - 2026)
16 küresel enstrümanın 5 yıllık asimetrik konveksite performansını, kâr faktörünü ve ASCII getiri eğrisini test etmek için:
`powershell
python run_convexity_backtest.py
`

### B) Canlı Günlük Piyasa Tarayıcısı (Live Scanner)
Bugün kırılım veren taze işlemleri ve kırılmaya en yakın radardaki varlıkları anında listelemek için:
`powershell
python live_monitor.py
`
*Üretilen tüm taze sinyaller anında signals/active_signals.json dosyasına da yazılır.*

### C) MT5 Yaşam Döngüsü Daemon'u (Canlı İnfaz & Takip)
MetaTrader 5 terminali açıkken emir iletimini, trailing stop güncellemelerini ve Telegram bildirimlerini başlatmak için:

**Test / Simülasyon Modu (Dry-Run - Emirsiz Test):**
`powershell
python execution/lifecycle_daemon.py
`

**Canlı Otomatik İnfaz Modu:**
`powershell
python execution/lifecycle_daemon.py --live
`

---

## 📡 10. Telemetri ve Bildirim Sistemi

Sistem, kritik olayları anlık olarak Telegram kanalınıza formatlı HTML mesajlarıyla ulaştırır:

- 🟢 **Yeni İşlem İnfazı:** Bilet A (+2.0R TP) ve Bilet B (Trailing Runner) lotları, slippage sapması ve spread verisiyle bildirilir.
- 🛡️ **Trailing Stop Güncellemesi:** Bilet B'nin yeni kâr kilitleme seviyesi bilet numarasıyla Telegram'a basılır.
- 💰 **Kâr Kilitleme / Kapanış:** İşlem kapandığında net elde edilen $ getirisi ve dolar karşılığı özetlenir.
- 📜 **JSONL Denetim Defteri:** Tüm raw olaylar logs/execution_audit.jsonl dosyasına yazılır.

---

## ⚖️ 11. Güvenlik ve Risk Feragatnamesi

> [!WARNING]
> **Finansal Risk Uyarısı:** Bu yazılım finansal tavsiye niteliği taşımaz. Vadeli işlem sözleşmeleri, döviz (forex) ve kripto para piyasaları yüksek düzeyde sermaye kaybı riski içerir. Canlı hesaplarda çalıştırmadan önce yeterli süre demo hesaplarda test edilmesi şiddetle önerilir.

---

**Geliştirici:** Ömer ([@Piardian](https://github.com/Piardian))  
**Ekosistem:** Begonya Quantitative Macro & Convexity Suite
