# Keenetic Hue

![Keenetic Hue — Hue BLE, Entware ve Google Home arasında kontrollü bağlantı](docs/assets/hero.png)

**Hue BLE lambanı Keenetic üzerinden Google Home'a bağla.**

USB Bluetooth adaptörü takılı Keenetic üzerindeki Entware ajanı lambayı yönetir.
Cloudflare Worker ve dışarı doğru kurulan Tunnel, Google Home komutlarını yerel
ajana ulaştırır; router'da gelen port açılması gerekmez.

[English overview](README.en.md) · [Kurulum](docs/installation.md) · [Güvenlik](SECURITY.md) · [RC12 notları](docs/releases/0.2.0-rc.12.md)

> **Durum:** Kişisel kurulumda kabul edilmiş RC12 adayı. Evrensel Hue Bridge
> alternatifi veya Google sertifikalı ürün değildir. Farklı donanım ve yeni
> Google/Cloudflare hesaplarıyla uçtan uca ilk kurulum ayrıca doğrulanmalıdır.

## Neler yapar?

- Açma/kapatma ve beyaz ışık parlaklığı kontrolü.
- Tek kullanımlık kodla kişisel Google Home hesap bağlama.
- Bağlantı kaybında sınırlı yeniden deneme; Entware açılışında otomatik başlama.
- İmzalı sürümler, geri alma, şifreli yedek ve aynı kurulumun geri yüklenmesi.
- Ayrı servis kimlikleri; belirsiz fiziksel komutları tekrar göndermeyen kayıt sistemi.
- Özgün SQLite/journal çiftini koruyan, geçici kopyada yetkisiz süreçle kurtarma.

## Nasıl bağlanır?

![Google Home → Worker → Tunnel → Entware ajanı → Bluetooth Hue lambası](docs/assets/architecture.svg)

OAuth ve HomeGraph bulutta, BLE bond ve yerel kontrol Keenetic'te kalır.
[Mimari ve güven sınırları](docs/architecture.md).

## Doğrulanan profil

| Bileşen | Kabul edilen kurulum |
| --- | --- |
| Lamba | Philips Hue White LWA017 — BLE, aç/kapat ve parlaklık |
| Bluetooth | Belkin BCM20702A0 USB adaptörü |
| Router ortamı | Keenetic + ext4 USB üzerinde Entware, `mips-3.4` |
| Çalışma zamanı | CPython 3.13; kesin sürümler [kilit dosyasında](requirements-router.lock.json) |
| Bulut | Kendi Cloudflare Worker/Tunnel ve Google Home geliştirme projen |

RC12 mevcut kurulumda komut ve router reboot kabulünü geçti. Bu sonuç bütün
Keenetic/Hue ürünleri için uyumluluk garantisi değildir. Renk, Matter ve ses
aktarımı doğrulanmış kapsamda değildir.

## Nereden başlamalıyım?

| Yapmak istediğin | Kılavuz |
| --- | --- |
| Boş Entware ortamına imzalı yazılımı kurmak | [Kurulum](docs/installation.md) |
| Aynı adaptör ve ampulle yeni USB’ye geçmek | [Yedekten yeniden kurulum](docs/reinstall.md) |
| Kendi Cloudflare ve Google Home kaynaklarını hazırlamak | [Bulut kurulumu ve kurtarma](docs/cloud-rebuild.md) |
| Google Home’a bağlamak veya yeni kod almak | [Google Home hesabını bağlama](docs/google-home.md) |
| Durumu kontrol etmek, yedek almak veya güncellemek | [Günlük bakım](docs/maintenance-quickstart.md) |
| Bir arızayı veya çevrimdışı durumu incelemek | [Sorun giderme](docs/troubleshooting.md) |
| Gelişmiş USB bakımı için RAM araç paketini hazırlamak | [Yerel ayarlar ve bakım yardımcısı](docs/storage-maintenance.md) |

İmzalı Keenetic paketi, açık doğrulama anahtarı ve bağımsız kurucu birlikte kullanılır. Keenetic paketinin sürümü ile Worker sürümü ayrı yönetilir. Google Home’daki geliştirme entegrasyonu `[test] Keenetic Hue` adıyla görünür.

Boş Entware’e yazılım kurulumu, BLE eşleştirmesi ve bulut yetkilendirmesi ayrı aşamalardır. İlk defa kurulacak bir sistemde, imzalı paket dışında kendi donanımın için BLE eşleştirmesi ve özel yapılandırma gerekir. Bu depoda doğrulanmış otomatik başlatma yolu, aynı kurulumun şifreli yedeğini geri yüklemektir.

Yerel kabul için Entware terminalinde `huectl health --live --json` çalıştır. Google Home kontrolünü ve durum bildirimini ayrıca doğrula.

## Kaynak, katkı ve destek

`hue_agent/` BLE ajanını, `hue_admin/` servis/bakım sınırlarını, `cloud/` Google
Home katmanını ve `deploy/` imzalı paket araçlarını içerir. Kaynak testleri
fiziksel cihaz kabulünün yerini tutmaz; [katkı kılavuzuna](CONTRIBUTING.md) bak.

Hata bildiriminde token, özel anahtar, bond, kurulum kodu veya yedek paylaşma.
[Güvenlik bildirimleri](SECURITY.md) · [Destek](SUPPORT.md) · [Sunum araştırması](docs/repository-design.md)

Proje kodu [MIT lisanslıdır](LICENSE). Diğer bileşenler kendi lisanslarını korur;
[üçüncü taraf açıklamaları](THIRD_PARTY_NOTICES.md). Ürün adları ilgili hak
sahiplerine aittir; proje bu kuruluşların resmî veya onaylı ürünü değildir.
