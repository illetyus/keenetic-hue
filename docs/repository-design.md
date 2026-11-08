# Depo Sunumu: Ampirik Yazılım Mühendisliği Literatürü ve Mimari Kararlar

**İnceleme ve Uygulama Tarihi:** 8 Kasım 2026  
**Kapsam:** Açık kaynaklı sistem/altyapı depolarında bilgi mimarisi, görsel iletişim, kurulum bilişsel yükü, özellik–kod izlenebilirliği (traceability) ve depo hijyeni.  
**Sınır:** Bu belge depo sunumu ve dokümantasyon mühendisliği kararlarını açıklar; fiziksel cihaz kabulünün veya bağımsız kriptografik güvenlik denetiminin yerini tutmaz.

---

## 1. Ampirik Literatür ve Kavramsal Çerçeve

Açık kaynaklı yazılım depolarında ilk izlenim, güvenilirlik algısı ve geliştirici/operatör adaptasyonu üzerine yapılan ampirik yazılım mühendisliği çalışmaları, rastgele süslemeler yerine **yapılandırılmış kanıt ve düşük bilişsel yükün** belirleyici olduğunu göstermektedir:

### 1.1 Bilgi Kategorileri ve "Why / Status" Boşluğu
- **Prana, Treude, Thung, Atapattu & Lo (2019)** — *Categorizing the Content of GitHub README Files* (*Empirical Software Engineering*, 24(3), 1296–1327; [arXiv:1802.06997v2](https://arxiv.org/html/1802.06997v2)): 393 depodaki 4.226 README bölümünü inceleyen çalışma, projelerin büyük çoğunluğunun **Ne (`What`)** ve **Nasıl (`How`)** sorularını yanıtladığını; buna karşılık **Neden (`Why` — alternatiflere göre tasarım gerekçesi)**, **Durum (`Status` — kanıtlanmış kapsam ve sınırlar)** ve **Katkı/Yönetişim (`Contribution`)** bölümlerinin sıkça eksik bırakıldığını ortaya koyar.
- **Bu Depodaki Karşılığı:** Giriş bölümünde projenin neden standart bir Hue Bridge veya yerel port yönlendirme yerine *Keenetic USB BLE + dışarı doğru Cloudflare Tunnel + ayrılmış servis kimlikleri + imzalı sürüm (`huerel`)* mimarisini seçtiği karşılaştırmalı olarak açıklanmış; doğrulanmış donanım/yazılım profili ile kapsam dışı alanlar açıkça ayrılmıştır.

### 1.2 Görsel Öğeler, Kod Blokları ve Yapısal Düzenin Etkisi
- **Venigalla & Chimalakonda (2022)** — *What Makes a Good GitHub Project README? An Empirical Study on Correlation Between README Content and Project Popularity* ([arXiv:2206.10772v1](https://arxiv.org/html/2206.10772v1)): 10 programlama dilinde 1.950 depoyu inceleyen çalışma; **yapılandırılmış listeler**, **navigasyon bağlantıları**, **statik mimari diyagramlar**, **kod/komut örnekleri** ve **referans/katkı bölümlerinin** proje benimsenmesi ile anlamlı pozitif korelasyona sahip olduğunu; buna karşılık salt dekoratif GIF/video öğelerinin sistem dillerinde anlamlı bir katkı sağlamadığını raporlar.
- **Wang, Wang, Liu, Zhou & Jin (2023)** — *How README Files Affect the Popularity of GitHub Repositories* (*Journal of Systems and Software*, 204, 111806; [DOI:10.1016/j.jss.2023.111806](https://www.sciencedirect.com/science/article/abs/pii/S0164121223002017)): 5.000 depo üzerinde yapılan analiz, metin okunabilirliği, tablo/liste dengesi ve somut kullanım/doğrulama çıktılarının ziyaretçi güvenini doğrudan artırdığını doğrular.
- **Bu Depodaki Karşılığı:** Yanıltıcı arayüz maketleri veya sahte durum rozetleri yerine; güven sınırlarını, UID/GID ayrımını (`hue-agent:61001`, `hue-tunnel:61000`), yerel loopback/Unix soket uçlarını ve atomik SQLite kurtarma akışını gösteren özgün teknik diyagramlar ([`docs/assets/architecture.svg`](assets/architecture.svg) ve [`docs/assets/hero.png`](assets/hero.png)) ile gerçek `huectl health --live --json` doğrulama şeması kullanılmıştır.

### 1.3 Kurulum Dokümantasyonunda Bilişsel Yük Yönetimi
- **Gao, Treude & Zahedi (2025)** — *Evaluating Setup and Installation Instructions in GitHub READMEs* ([arXiv:2312.03250v3](https://arxiv.org/html/2312.03250v3)): 400 Python ve Java deposundaki 1.163 kurulum değişikliğini inceleyen çalışma, tüm kurulum ayrıntılarını ana README'ye yığmanın **bilgi aşırı yüklemesi (information overload)** yarattığını gösterir. En etkili örüntü; ana sayfada süreci **(1) Ön Koşullar (Pre-installation)**, **(2) Kurulum (Installation)** ve **(3) Kurulum Sonrası Doğrulama (Post-installation Verification)** olarak özetleyip adım adım işlemleri görev odaklı alt kılavuzlara ayırmaktır.
- **Diátaxis Çerçevesi** ([diataxis.fr](https://diataxis.fr/)): Kurulum ([`docs/installation.md`](installation.md)), yedekten dönüş ([`docs/reinstall.md`](reinstall.md)), günlük operasyonel hızlı başvuru ([`docs/maintenance-quickstart.md`](maintenance-quickstart.md)), sorun giderme ([`docs/troubleshooting.md`](troubleshooting.md)) ve mimari açıklama ([`docs/architecture.md`](architecture.md)) belgelerinin birbirine karıştırılmadan ayrıştırılmasını sağlar.

### 1.4 Özellik–Kod İzlenebilirliği (Feature-to-Code Traceability)
- **Puhlfürß, Montgomery & Maalej (2022/2024)** — *An Exploratory Study of Feature Visuals and Code Links in README Files* ([arXiv:2208.01317v1](https://arxiv.org/html/2208.01317v1)): Yüksek yıldızlı 25 popüler açık kaynak deposundaki 480 özellik açıklamasını inceleyen çalışma, anlatılan özelliklerin **%32'sinde hiçbir kullanım veya kaynak kod bağlantısı bulunmadığını** tespit eder. Özellik özetlerini doğrudan ilgili kaynak modüllere ve doğrulama testlerine bağlamak hem kullanıcı güvenini hem de kod inceleme hızını artırır.
- **Bu Depodaki Karşılığı:** Ana README'de her temel mühendislik ve güvenlik özelliği (Ed25519 imzalı paketleme, ayrılmış servis kimlikleri, opak SQLite kurtarma, belirsiz fiziksel komut koruması, OAuth/HomeGraph köprüsü) doğrudan ilgili kaynak dosyasına ve birim/entegrasyon testine bağlanmıştır.

### 1.5 Standart Depo Anatomisi ve Sürdürülebilirlik Sinyalleri
- **Hora, Montandon & Costa (2026)** — *Anatomy of GitHub Repositories* ([arXiv:2605.16701v2](https://arxiv.org/html/2605.16701v2)) ve **Gaughan, Champion & Hwang (2025)** — *Governance and Maintenance Signals in FLOSS Repositories* ([arXiv:2502.18440v2](https://arxiv.org/html/2502.18440v2)): 10.000 aktif depo ve 4.226 üst-akış (upstream) FLOSS paketini inceleyen çalışmalar; standart kök dosyaların ([`README.md`](../README.md), [`LICENSE`](../LICENSE), [`SECURITY.md`](../SECURITY.md), [`CONTRIBUTING.md`](../CONTRIBUTING.md), [`SUPPORT.md`](../SUPPORT.md), `.github/workflows`, `.github/ISSUE_TEMPLATE`) öngörülebilir konumlarda bulunmasının ve otomatik CI doğrulamalarının ("ritüel" rozetler yerine gerçek çalışan kontrollerin) proje olgunluğunun temel göstergesi olduğunu vurgular.

---

## 2. Alan Emsalleri (Domain Precedents)

- **[Google Bumble](https://github.com/google/bumble):** USB HCI üzerinden kullanıcı alanında Bluetooth yığını çalıştırma modelini, donanım/taşım (transport) gereksinimlerini ve kapsam sınırlarını net biçimde ayırması nedeniyle referans alınmıştır. Bumble'ın genel Bluetooth kapsamı bu projenin destek iddiası değildir.
- **[bleak-esphome](https://github.com/Bluetooth-Devices/bleak-esphome):** Yerel BLE radyo katmanı ile üst katman otomasyon köprüsünü birbirinden ayırma ve görev odaklı kurulum bağlantıları sunma yaklaşımı açısından incelenmiştir.

---

## 3. Uygulanan Somut Tasarım Kararları

| Tasarım Boyutu | Literatür Dayanağı | Uygulanan Karar |
| --- | --- | --- |
| **Giriş ve Konumlandırma (`Why` & `Status`)** | Prana et al. (2019) | Standart kurulumlara göre fark tablosu ve RC12 kabul sınırları ilk ekranda sunulur; doğrulanmamış donanım/hesap senaryoları gizlenmez. |
| **Teknik Görseller (`hero.png`, `architecture.svg`)** | Venigalla & Chimalakonda (2022); GitHub Social Preview (1280×640, <1 MB) | Haricî ticari logo içermeyen, güven sınırlarını (`Cloudflare` ↔ `Keenetic Entware` ↔ `Hue BLE`), UID/GID izolasyonunu ve yerel portları gösteren özgün vektörel/PNG diyagramlar üretilmiştir. |
| **3 Aşamalı Kurulum ve Doğrulama** | Gao et al. (2025); Diátaxis | README'de Ön Koşul → İmzalı Kurulum → `huectl health --live --json` Doğrulama akışı özetlenmiş; detaylar 7 görev kılavuzuna dağıtılmıştır. |
| **Özellik → Kod → Test Matrisi** | Puhlfürß et al. (2022/2024) | README içinde her kritik güvenlik/dayanıklılık mekanizmasını `hue_agent/`, `hue_admin/`, `cloud/` ve `tests/` altındaki somut dosyalara bağlayan izlenebilirlik tablosu eklendi. |
| **Yayın Hijyeni ve Kanıt Ayrımı** | Hora et al. (2026); [`tools/check_publication.py`](../tools/check_publication.py) | Özel anahtar, BLE bond, token, kişisel yedek (`.huebak`) ve geliştirici makine yolları otomatik olarak denetlenir; taşınabilir kaynak testleri (81 Python + 64 Worker) ile fiziksel cihaz kabulü birbirine karıştırılmaz. |
