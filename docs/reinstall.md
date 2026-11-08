# USB/Entware kaybından sonra yeniden kurulum

Bu kılavuz yeni Entware USB’sine imzalı RC12 yazılımını kurup aynı cihazın şifreli yedeğini geri yükleme yolunu anlatır. USB biçimlendirmeden önce doğru fiziksel diski doğrula.

Bu yol, aynı Bluetooth adaptörü ve aynı ampulün şifreli özel yedeği içindir. Ampul sıfırlanmaz; mevcut Google/Cloudflare kaynakları korunur. Farklı adaptör veya ampul için eski bond'un çalışacağı varsayılmaz. Ajan gerçek adaptörün public adresini yapılandırmadaki kimlikle karşılaştırır; uyuşmazlıkta kontrol başlamaz.

## 1. USB dışında hazır tutulacaklar

- Kişisel parolayla oluşturulmuş `.huebak` ve parolası; birbirinden ayrı güvenli yerlerde.
- İmzalı `.huerel`, açık doğrulama anahtarı, doğrulanmış açık anahtar parmak izi ve bağımsız `install_release.py`.
- Bu kılavuz, `requirements-router.lock.json` ve `requirements-pure-router.txt`.
- Cloudflare/Google hesap erişimi. Bu cihaz yedeği buluttaki geri okunamayan secret'ların tam yedeği değildir.

Bir yazılım paketi tek başına BLE bond, token veya Google bağlantısını yeniden oluşturmaz. Özel yedek kayıpsa yeniden yetkilendirme/eşleştirme ayrı işlemdir.

## 2. Entware'i hazırlama

USB'yi ext4 hazırlama ve Entware kurma işlemini kendi Keenetic modelinin [resmî OPKG](https://help.keenetic.com/hc/tr/articles/360000948719-OPKG) ve [USB'ye Entware kurulum](https://destek.keenetic.com.tr/titan/kn-1811/tr/20980-installing-the-entware-repository-on-a-usb-drive.html) adımlarına göre yap. Bu işlem USB'deki eski verileri silebileceğinden doğru fiziksel diski ayrıca doğrula. Windows DiskPart'ın ext4 biçimlendirme yaptığı varsayılmaz.

Keenetic'te Açık Paket Desteği ve EXT dosya sistemi bileşenleri gerekir; OPKG sayfasında ilgili USB sürücü seçilir. Kurucu arşivi cihazın gerçek CPU mimarisine göre seçilir. Bağlantılı kılavuzdaki model örneği bu projenin paket profili değildir. Bu ilk Hue paketi `mips-3.4`/CPython 3.13 içindir. Entware kurulumu tamamlanınca SSH portunu sistemin gerçek ayarına göre kullan; bu kurulumda port 222 kullanıldı. Resmî belge portun Keenetic SSH bileşenine göre 22 veya 222 olabildiğini açıklar.

SSH anahtar değişimi gerçek yeniden kurulumdan kaynaklanıyorsa yeni parmak izini cihazın güvenilir yerel arayüzünden doğrula. Eski kaydı sorgusuz kaldırma. Entware parolasını kendin belirle.

## 3. Çalışma zamanı bağımlılıkları

Yeni Entware SSH oturumunda:

```sh
opkg update
opkg print-architecture
opkg install python3 python3-pip python3-cryptography libusb-1.0 ca-bundle
python3 --version
python3 -c 'import cryptography; print(cryptography.__version__)'
```

`cryptography`, bu MIPS hedefinde Entware'in derlenmiş paketiyle kurulur. Python/Rust derlemesi router üzerinde başlatılmaz. Saf Python gereksinim listesini özel kurulum klasörüne SFTP ile kopyaladıktan sonra:

```sh
python3 -m pip install --no-cache-dir --only-binary=:all: \
  -r /opt/var/tmp/hue-install/requirements-pure-router.txt
python3 -m pip install --no-cache-dir --no-deps bumble==0.0.236
```

Liste, gerçek cihazda doğrulanmış BLE ajanı bağımlılıklarını sabitler. Bumble'ın kullanılmayan web uygulamaları için ek bağımlılıklar bu profilin parçası değildir. Sabitlenmiş native sürümler `requirements-router.lock.json` içindedir. Entware deposu daha yeni sürüme geçtiyse imzalı paket bunu reddedebilir; kontrolü devre dışı bırakmak yerine yeni profil için test edilmiş bir paket hazırlanır. Bu işlem Keenetic firmware'ini veya diğer sistem paketlerini topluca güncellemez.

## 4. Yazılımı kurma

[İmzalı yazılım hazırlama](installation.md#imzalı-yazılımı-hazırlama) komutunu çalıştır. Yedek dosyasını SFTP ile root'a özel bir klasöre kopyala ve dosyaya yalnız root erişimi ver:

```sh
mkdir -p /opt/var/lib/keenetic-hue-system/backups
chmod 700 /opt/var/lib/keenetic-hue-system/backups
chmod 600 /opt/var/lib/keenetic-hue-system/backups/hue-01.huebak
```

## 5. Boş kurulumda önce doğrulama, sonra geri yükleme

Bu komutlar birleşik düzenin **10 dosyalı yedeğini** kabul eder. Eski 9 dosyalı düzenin yedeği bu işlemle otomatik dönüştürülmez. `VERSION`, kurduğun doğrulanmış yazılım yuvasıdır:

```sh
huectl restore /opt/var/lib/keenetic-hue-system/backups/hue-01.huebak \
  --initialize --release-id VERSION --dry-run
```

Parolayı terminalde gizli girişe yaz. Doğrulama başarılıysa:

```sh
huectl restore /opt/var/lib/keenetic-hue-system/backups/hue-01.huebak \
  --initialize --release-id VERSION
huectl health --live --json
```

İlk doğrulama dosya yazmaz veya servis başlatmaz. Uygulama aşamasında hedefte mevcut yapılandırma/kimlik varsa işlem reddedilir; çalışan kurulumu boş kurulum gibi ezmez. İmza ve durum şeması, şifreli kimlik, kesin dosya listesi, sabit dosya yolları, token biçimleri ve gerçek uygulamanın SQLite şeması doğrulanır. Yedekteki açık doğrulama anahtarı kurulumda sabitlenen anahtarla aynı olmalıdır.

Yeni sahipler başladıktan sonra 90 saniyeye kadar BLE/Tunnel ve doğru sürüm sağlığı beklenir. Bu adım fiziksel adaptör kimliği kontrolünü ve mevcut bond ile şifreli bağlantıyı da sınar. Lamba için test aç/kapat komutu üretilmez. Doğrulanan ampulün fiziksel elektriği bu kabul sırasında açık olmalıdır.

Yarım veya başarısız ilk geri yükleme, yeni sahipleri durdurur; kısmi yapılandırmayı `backups/initialize-*` altında korur ve boş kurulum durumuna döner. Elektrik kesintisinden sonra başlangıç girişi veya `huectl recover` aynı makbuzu işler. Parolayı veya yedeği destek mesajına koyma.

## 6. Google Home kabulü

Önce `health --live --json` sonucunda doğru sürüm ve `healthy: true` gör. Sonra Google Home'dan istediğin tek bir komutu dene. Aynı bulut kaynakları, geçerli token'lar ve OAuth bağlantısı korunmuşsa yalnız USB değişmesi yeniden hesap bağlamayı gerektirmez. Hesap bağlantısı kaldırılmışsa `huectl setup-code` ile yeni kod üret ve [Google Home kılavuzunu](google-home.md) izle.

İnternet, DNS veya iptal edilmiş token nedeniyle bulut erişimi yoksa yerel geri yükleme dosyaları bunu tek başına düzeltemez. Worker/Tunnel/Google kaybı [ayrı bulut kurtarma kapsamıdır](cloud-rebuild.md).

## Kabulün sınırı

İşlevsel prova cihaz üzerinde ayrı geçici dizinler ve sahte ajan/Tunnel uçlarıyla yapılır; aynı makinedeki gerçek üretim portları ve Bluetooth adaptörü kullanılmaz. Gerçek USB'yi silerek yapılan bir prova iddia edilmez. RC11 için mevcut cihazda yeniden başlatma ve kişisel yedeğin geçici bellekte doğrulanması ayrı ayrı kabul edildi. Yeni fiziksel USB üzerinde gerçek geri yükleme bu kontrollerden ayrıdır.
