# Yedek, geri yükleme ve yeni USB

Bir sürüm paketini kullanırken paketin kurulum komutunu, doğrulama
anahtarı özetini ve sürüm kimliğini esas al. Kişisel arşivi doğrulamak için
aşağıdaki kuru çalıştırmayı kullan. Bu adım arşivi açar ve dosyaları doğrular.

## Kişisel şifreli yedek

Keenetic'in anahtarla giriş yaptığın Entware terminalinde:

```sh
mkdir -p /opt/var/lib/keenetic-hue-system/backups
chmod 700 /opt/var/lib/keenetic-hue-system/backups
huectl backup /opt/var/lib/keenetic-hue-system/backups/benim-yedegim.huebak
```

En az 16 karakterli yedek parolasını terminalde belirle. Parola ve yedeği ayrı
sakla; ikisini de GitHub'a koyma. Aynı dosya adına ikinci kez yazılmaz.
Dosyayı SFTP ile PC'ye alıp USB'den ayrı bir yerde koru. Yeni USB'yi hazırlamadan
önce bu kopyanın mevcut olduğundan emin ol.

Yedek; canonical ayarlar, adaptör/ampul kimliği, BLE bond, API/Tunnel/kurulum
kodu bağlantı sırları, release trust public key ve komut günlüğünü içerir.
Aktif ayrı kullanıcı ajanının en güncel verisi alınır. Günlük/cache ve sürüm
binary'leri yedeğe eklenmez. SSH özel anahtarları, SSH host key, modem ayarları,
Cloudflare Worker veritabanı ve Google projesi bu dosyanın kapsamında değildir.

## Aynı çalışan kurulumda geri yükleme

Önce doğrulama; servisler durmaz:

```sh
huectl restore /guvenli/yol/benim-yedegim.huebak --dry-run
```

Yedek parolasını gizli terminal istemine gir. Başarılı yanıt `verified: true`
ve `installationChanged: false` gösterir.

Doğrulama başarılıysa:

```sh
huectl restore /guvenli/yol/benim-yedegim.huebak
huectl health --live --json
```

Servis durdurulur, güncel veri geri dönüş için korunur, yedek uygulanır ve
yeniden başlayan sürümün sağlığı denetlenir. Normal geri yüklemenin başlatması
başarısızsa geri yükleme öncesindeki güncel kayıtlarla geri dönülür. İşlem
kesilirse dosyaları elle taşımak yerine:

```sh
huectl restore --recover
huectl health --live --json
```

Kaybolmuş veya bozulmuş aktif journal, eski master dosyadan kendiliğinden
oluşturulmaz. Bu hata güvenlik açısından kasıtlı olarak başlangıcı durdurur.
Bozuk aktif durumdan yerinde zorla geri yükleme bu aşamada tamamlanmış bir
onarım yolu değildir; sağlam yedeği koruyup aşağıdaki temiz kurulum yolunu kullan.

## Yeni USB / temiz Entware

1. Şifreli yedeği ve parolasını, güvenilir imzalı sürüm paketini ve public key
   özetini USB'den ayrı sakla. Aynı Bluetooth adaptörünü ve ampulü koru.
2. Yeni USB üzerinde Entware'i kur; paketin Python/platform sürüm kilidiyle
   uyumunu sağla. İmzalı kurucu bağımlılıkları kendiliğinden yükseltmez.
   Termius anahtarını ve yeni SSH host fingerprint'ini kendi doğrulamanla kur.
3. Yeni USB kurulumundan sonra Keenetic'i normal biçimde yeniden başlat.
   Önceki kurulumun geçici native hesap satırları aynı oturumda kalabilir.
4. Sürümün yayımlanan güvenilir kurucusunu ve public key doğrulamasını kullan.
   Kurucu imzayı doğrular; sürümü, yönetim komutunu ve ayrı servis hesaplarını
   hazırlar. Henüz ampulü sıfırlamak veya yeniden eşleştirmek gerekmez.
5. Şifreli yedeği özel bir dizine kopyala ve sürümün gerçek kimliğini kullan:

```sh
huectl restore /guvenli/yol/benim-yedegim.huebak --initialize --release-id SURUM_KIMLIGI --dry-run
huectl restore /guvenli/yol/benim-yedegim.huebak --initialize --release-id SURUM_KIMLIGI
huectl health --live --json
```

`SURUM_KIMLIGI` bir örnek yer tutucudur; paketteki sürümle değiştirilmelidir.
Temiz kurulum, yedeğin sayısal Linux kullanıcılarını kopyalamaz. Yeni kimlikler
çakışma denetiminden geçirilir; ajan verilerinin sahibi güvenli hazırlıkla atanır.
Ana uygulama köküne veya USB ağacına toplu `chown` uygulanmaz.

`ACCOUNT_UNPROFILED_NAME`, hesapların mevcut olduğunu fakat güvenilir kimlik
profilinin bulunmadığını söyler. Kurucu mevcut hesabı devralmaz ve satırları
silmez. Yeni USB/yeni Entware ise normal yeniden başlatmadan sonra aynı imzalı
kurucuyu yinele. Eski Entware hesabını koruyan veya uygulama profili silinmiş bir
kurulumda hesap dosyalarını elle temizleme; sağlam kurulum/yedeği koru ve temiz
USB yolunu kullan. Farklı Bluetooth adaptörü, ayrı ve bilinçli eşleştirme/göç
işlemidir; eski bond'un başka denetleyicide çalışacağı varsayılmaz.

## Kod ve sürüm geri dönüşü

Önceki doğrulanmış sürüm mevcutsa:

```sh
huectl rollback
huectl health --live --json
```

İmzalı kod slotu veya yeniden üretilebilir runtime dosyalarının izinleri
bozuksa aynı sürümün doğrulanmış paketini kullan:

```sh
huectl repair /guvenli/yol/ayni-surum.huerel
```

Yeni uygulamada onarım servisleri durdurur, güncel ajan verisini korur ve
yalnız sabit kod/ayar/credential projection alanları ile runtime run/log/cache
alanlarını root'a özel karantinaya taşır. İmzalı kod ve root master ayarlardan
bu alanları yeniden oluşturur; eski dosyaları topluca chown etmez. Bond ve
komut günlüğü bu karantinaya taşınmaz. Başarı, yeniden başlayan sürümün sağlık
kontrolünü gerektirir. Bu davranış ayrı UID'li sahte servislerde doğrulandı;
canlı dağıtım kabulü henüz yapılmadı.

Onarım yarıda kalırsa başka sürüme geçmeden önce:

```sh
huectl recover
huectl health --live --json
```

Onarımın bozuk mutable journal'ı, kayıp bond'u veya kayıp kimlik profilini
onaracağı varsayılmaz. Karantina özel sır kopyaları içerebilir; paylaşma veya
GitHub'a ekleme. Kod onarımı, arşiv geri yükleme ve yeni USB kurulumu farklı
işlemlerdir; Google Home hesabını gereksiz yere yeniden bağlama.
