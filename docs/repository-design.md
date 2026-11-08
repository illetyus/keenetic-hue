# Depo sunumu: araştırma ve uygulanan kararlar

İnceleme tarihi: 8 Kasım 2026. Kapsam: README bilgi mimarisi, benzer BLE
projelerinin sunumu, GitHub yayın/güvenlik rehberleri ve dokümantasyon araştırması.
Bu not bir sistem güvenlik taraması veya kapsamlı sistematik literatür incelemesi değildir.

## Literatür ve birincil kaynaklar

1. Prana, Treude, Thung, Atapattu ve Lo, *Categorizing the Content of GitHub
   README Files*: 393 depodaki 4.226 bölümü inceleyen çalışma, ne/nasıl bilgisinin
   yaygın; amaç ve durum bilgisinin daha seyrek olduğunu raporlar. Bu bulguya
   dayanarak amacı, kabul edilmiş sürümü ve doğrulanmamış senaryoları girişe
   koyduk. Bu çalışma görsel eklemenin yıldız veya kullanım artışı sağlayacağını
   kanıtlamaz. [Makale](https://arxiv.org/html/1802.06997v2).
2. GitHub, README'de amaç, yarar, başlangıç, yardım ve bakım bilgisini önerir;
   kısa giriş ve göreve göre kılavuz bağlantıları bu çerçeveyi izler.
   [README rehberi](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes).
3. Diátaxis; öğretici, görev kılavuzu, başvuru ve açıklama ihtiyaçlarını ayırır.
   Kurulum, günlük bakım, komut açıklamaları ve mimariyi ayrı belgelerde
   tutma kararımız bu çerçeveden yararlanır. [Özgün çerçeve](https://diataxis.fr/).
4. GitHub'ın depo rehberi, lisans/katkı/güvenlik belgelerini ve kullanılabilir
   güvenlik özelliklerini önerir. Belgeleri hazırladık; hesabın güvenlik
   ayarları etkinleştirildi diye doğrulanmadan iddia etmiyoruz.
   [Depo rehberi](https://docs.github.com/en/repositories/creating-and-managing-repositories/best-practices-for-repositories).
5. GitHub sosyal önizleme için 1 MB altı ve tercihen 1280×640 görsel önerir.
   Özgün görselimiz bu boyutta, sabit arka planlı ve haricî logo kullanmaz.
   [Önizleme rehberi](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview).

## Emsaller

- [Google Bumble](https://github.com/google/bumble): kapsam, bağımlılıklar, USB
  kullanımı ve örnekleri ayrı anlatması, doğrulanan donanımı görünür tutmamıza
  örnek oldu. Bumble'ın geniş protokol desteği bu projenin destek iddiası değildir.
- [bleak-esphome](https://github.com/Bluetooth-Devices/bleak-esphome/blob/main/README.md):
  BLE ağ geçidi rolünü kısa açıklaması ve kurulum bağlantılarını ayırması
  giriş metnine örnek oldu. Bu proje ESPHome kullanmaz.

## Uygulama

Türkçe ana README ve İngilizce giriş, orijinal hero/mimari görseli, ayrı
güvenlik/katkı/destek belgeleri, MIT lisansı ve gerçek ikiliye ait üçüncü taraf
notları hazırlandı. Başarı rozeti, cihaz ekran görüntüsü veya genel güvenlik
sertifikası uydurulmadı. RC12 bir kabul edilmiş adaydır; semantik sürümünü
görünüş için kararlı `1.0` olarak değiştirmedik.

Yayın seti yalnız incelenen dosyalardan hazırlanır; kişisel çalışma geçmişi,
kimlik bilgileri, yedekler, olay günlükleri ve iç uygulama planları taşınmaz.
Kaynak testleri, imzalı paket doğrulaması ve fiziksel kabul ayrı kanıtlardır.
