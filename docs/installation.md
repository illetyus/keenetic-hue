# Kurulum ve dosya düzeni

Bu kurulum yolu imzalı RC12 paketini ve bağımsız doğrulanmış açık anahtarı kullanır. Entware ve bağımlılıklar için [hazırlık adımlarını](reinstall.md#2-entwarei-hazırlama) izle.

Entware yönetim erişiminde anahtar kullanın ve SSH erişimini yönetim ağıyla sınırlayın.

Bu proje Keenetic'in Entware/Linux ortamında yerel süreçler olarak çalışır. Sistem dosyaları tek köktedir:

```text
/opt/var/lib/keenetic-hue-system/
  releases/VERSION/  imzalı, sürüme ait uygulama dosyaları
  manager/           seçili sürümü doğrulayan sabit CLI/kurtarma katmanı
  config/            cihaz, ajan, yönetim ve açık doğrulama anahtarı
  secrets/           BLE bond ve dar kapsamlı token dosyaları
  state/             komut günlüğü, şema ve bakım makbuzları
  run/               süreç kimlikleri ve kilitler
  logs/              sınırlandırılmış günlükler
  backups/           özel geri dönüş kopyaları ve şifreli yedekler
  active.json        seçili sürüm ve varsa önceki sürüm
```

`/opt/bin/huectl` yönetim girişidir. `/opt/etc/init.d/S97keenetic-hue`, Entware açılırken `internal-service start` çağırır. Ajan ve Tunnel ayrı yaşam süresi kilitleriyle gözetilir. Bir bileşenin yeniden başlaması diğerinin yeniden başlamasını zorunlu kılmaz. PID dosyasındaki sayıya tek başına güvenilmez; süreç argümanları, açılış kimliği ve süreç başlangıç zamanı doğrulanır.

`config.example.json`, yalnızca birleşik ajan yapılandırmasının alan ve dosya yolu örneğidir. İçindeki adapter/lamba adresleri kasıtlı olarak yer tutucudur; dosyayı çalışır yapılandırma diye kopyalama. Desteklenen yeniden kurulum, imzalı sürüm paketi ile aynı Hue kurulumuna ait şifreli yedeği kullanır; elle bond, adres veya token üretip yapılandırmaya yazma.

## Gerekli teslim dosyaları

- İmzalı `keenetic-hue-VERSION.huerel` yazılım paketi.
- `keys/release-trust.pub` açık anahtarı.
- `deploy/install_release.py` bağımsız, güvenilir kurulum/kurtarma aracı.
- Yeni USB'ye dönüş için ayrıca kişisel parolalı `.huebak` yedeği.

Yazılım paketi özel yapılandırma veya kimlik bilgisi taşımaz. İlk kurulumda açık anahtarın SHA-256 parmak izi ayrı bir güvenilir kaynaktan doğrulanır. Mevcut anahtar sessizce değiştirilmez. Bu geliştirme kurulumunun açık anahtar parmak izi:

```text
3eb6abbce938c6f75054da02ecca3cb291f576ef0633b5de54dd78fa2cd8a4be
```

Özel imzalama anahtarı cihazın günlük yönetimi için gerekmez. Güvenli bir yedekte tutulur; USB'ye, pakete veya depoya konmaz.

## İmzalı yazılımı hazırlama

Entware ve paket profilindeki bağımlılıklar kurulduktan sonra dosyaları SFTP ile yalnız root'un erişebildiği bir klasöre kopyala:

```sh
mkdir -p /opt/var/tmp/hue-install
chmod 700 /opt/var/tmp/hue-install
```

Arşivi ve açık anahtarı aktarım dizinine koy. Kurucuyu ayrı olarak, yalnız root'un yazabildiği `/opt/var/lib/keenetic-hue-trusted` dizinine aktar. Bu dizini root olarak `0700` izinleriyle oluştur ve üst dizinlerin de yetkisiz yazmaya kapalı olduğunu doğrula.

Güvenilen iş istasyonunda incelenmiş `deploy/install_release.py` kopyasının SHA-256 özetini `Get-FileHash -Algorithm SHA256 .\deploy\install_release.py` ile hesapla. `INSTALLER_SHA256` alanına bu özeti yaz. `VERSION` ve `PUBLIC_KEY_SHA256` alanlarını teslim edilen sürüm ve bağımsız doğruladığın açık anahtar parmak iziyle değiştir. Kurucu yalnızca özet kontrolü başarılı olursa çalışır:

```sh
printf '%s  %s\n' 'INSTALLER_SHA256' /opt/var/lib/keenetic-hue-trusted/install_release.py | sha256sum -c - && \
python3 -I /opt/var/lib/keenetic-hue-trusted/install_release.py \
  /opt/var/tmp/hue-install/keenetic-hue-VERSION.huerel \
  --trust-key /opt/var/tmp/hue-install/release-trust.pub \
  --trust-key-sha256 PUBLIC_KEY_SHA256
```

Bu adım paketi doğrular, sürüm yuvasını ve yönetim girişlerini kurar; boş kurulumda ampul kimliği veya token üretmez, ajanı kendiliğinden eşleştirmez. Mevcut bir cihazı taşıyorsan [özel yedekten yeniden kurulum](reinstall.md) adımlarına geç. Hiç yedeğin yoksa önce donanıma özgü BLE eşleştirmesi ve özel yapılandırma gerekir. Bu sürümde bunları sıfırdan üreten doğrulanmış bir kurulum komutu yoktur.

Desteklenen eski üç klasörlü kurulumdan ilk geçişte aynı komuta `--migrate-existing` eklenir. Bu seçenek rastgele dosya yollarını içe aktarmaz. Eski girişler yedeklenir, iki eski sahip durdurulur, adaptör kilidi denetlenir, kimlikler korunarak yeni köke kopyalanır. Yeni sürümün BLE ve Tunnel sağlığı doğrulanır; eski klasörler ancak sonra `backups/migration-*/legacy/` altına alınır. Başarısızlıkta geri dönülür. Mevcut birleşik kurulumda normal sürüm değişikliği için [güncelleme komutunu](update-rollback.md) kullan.

## Kurtarma katmanı yarıda kalırsa

Normal bakım kesintisinde `huectl recover` veya bir sonraki `S97` başlangıcı makbuzları işler. Yönetim klasörünün değişimi sırasında CLI dosyası geçici olarak yoksa aynı doğrulanmış arşiv, açık anahtar ve bağımsız `install_release.py` komutunu yeniden çalıştır. Eski yönetim kodu `backups/bootstrap-*` içinde korunur. Arşivi ve bağımsız aracı USB dışında da sakla.

İmza veya sürüm kontrolünü kapatarak kurtarma yapma. Hata çıktısı kimlik bilgilerini göstermez; önce dosyaların doğru sürüm/anahtara ait olduğunu, özel izinleri, boş disk ve RAM'i kontrol et. Modem fabrika ayarına döndürülmez; ampul sıfırlanmaz.
