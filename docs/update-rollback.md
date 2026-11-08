# İmzalı güncelleme, onarım ve geri alma

Bu yordamları imzası ve içeriği doğrulanmış paketlerle kullan. RC11 ile RC5'e
kontrollü geri dönüş ve ardından normal güncelleme yoluyla RC11'in etkinleştirilmesi
Keenetic üzerinde doğrulandı.

Yeni paketler imzalı `releaseSequence` içerir. Normal güncelleme daha düşük sırayı reddeder; aynı sırada farklı içerik kabul edilmez. `rollback` kayıtlı önceki sürümü seçer ve en yüksek kabul edilen sırayı düşürmez. Eski sıra numarasız paketler mevcut slot onarımı/kurtarma için saklanır. İlk güvenlik sürümüne geçişte yeni bağımsız installer ile yönetim doğrulayıcısı da yenilenmelidir.

RC3 veya daha eski, sıra alanını tanımayan bir sürümden ilk geçişte güvenilir bağımsız installer'ı yeni paket ve sabitlenmiş açık anahtarla çalıştırın. Installer yeni slotu ve özel recovery manager'ı hazırlar; henüz aktif eski manager yeni manifesti okuyamaz. Bu tek geçiş için yeni recovery manager üzerinden güncelleyin:

```sh
python3 -I -c 'import sys; sys.dont_write_bytecode=True; sys.path.insert(0,"/opt/var/lib/keenetic-hue-system/manager"); from hue_admin.cli import main; sys.exit(main(["update",sys.argv[1]]))' /opt/var/lib/keenetic-hue-system/backups/candidates/0.2.0-rc.4.huerel
```

Bu komutu yalnız bağımsız installer'ın imza ve açık anahtar parmak izi doğrulaması başarılı olduktan sonra kullanın. Yeni sürüm aktifken normal `huectl update` kullanılır. Eski RC3'e açık rollback yapıldıktan sonra RC4'e tekrar geçişte de bu uyumluluk yolu gerekebilir.

Güncellemeden önce kişisel parolalı yeni bir [yedek](backup-restore.md) al ve başka bir diskte sakla. Yazılım paketi ile özel yedek ayrı dosyalardır. Güncelleme yapılandırma veya bond dosyasını paketten değiştirmez.

## Güncelleme

İmzalı paketi SFTP ile özel bir klasöre kopyala:

```sh
huectl health --live --json
huectl update /opt/var/tmp/hue-install/keenetic-hue-VERSION.huerel
huectl health --live --json
```

İmza, tam dosya listesi/hash'ler, mimari, Python ABI, sabitlenmiş bağımlılıklar ve kalıcı durum şeması doğrulanır. Önce iki sahip durdurulur, sonra seçili sürüm atomik dosya değişimiyle seçilir. Ajan ve Tunnel başlatılır. Sağlık yanıtının gerçekten beklenen sürüm kimliğine ait olması gerekir; eski bir HTTP sunucusunun yanıtı yeni sürümün sağlığı sayılmaz.

Sağlık kabulü için en fazla 60 saniye beklenir. Başarısızlıkta yeni sahipler durdurulur ve önceki sürüme dönülür. Bu sırada Google Home kısa süre çevrimdışı görebilir. Bakım komutunun başarılı olması ampulün fiziksel elektriğinin açık olduğunu garanti etmez; `health --live` ayrı kontroldür.

## Önceki sürüme dönme

```sh
huectl rollback
huectl health --live --json
```

`active.json` içindeki önceki doğrulanmış sürüm seçilir. İmzalı yuvalar ve özel yedekler otomatik silinmez; en az iki iyi sürümü koru. Eski kalıcı durum şemasını okuyamayan sürüme geçiş desteklenmiş sayılmaz. Mevcut şema sözleşmesi sürüm 1'dir; veri dönüştürme gerektiren gelecekteki bir sürüm ayrıca planlanmalıdır.

## Bozuk yazılımı onarma

```sh
huectl repair /opt/var/tmp/hue-install/keenetic-hue-VERSION.huerel
huectl health --live --json
```

Aynı sürüm kimliği için farklı imzalı içerik kabul edilmez. Aynı doğrulanmış arşivle bozuk dosyalar yeniden kurulur. Aktif sürümün onarımında önce sahipler durdurulur; çalışan dosyaların üzerine parça parça yazılmaz. Arızalı eski dosyalar inceleme için özel konuma alınır.

## Elektrik kesilmesi veya yarım bakım

```sh
huectl recover
huectl health --live --json
```

Makbuzlar bootstrap, boş kurulum geri yükleme, eski düzenden taşıma, özel yedek geri yükleme, paket kurulumu ve etkin sürüm seçimi için ayrı tutulur. Her işlem tek bir atomik dosya değişimiymiş gibi sunulmaz. Tam doğrulanmış paket aşaması tamamlanabilir; yarım taşıma/geri yükleme güvenli önceki duruma döner. Eksik veya değiştirilmiş kurtarma girdisinde işlem devam ettirilmez. Gerekirse güvenilir bağımsız [kurulum aracını](installation.md) aynı arşivle yeniden çalıştır.

`SERVICE_ALREADY_MANAGED` yalnız beklenen sürümün sahip sürecinin zaten var olduğunu belirtir. BLE, Tunnel ve internet sağlığı için `health --live --json` kullan. `reporting: unknown`, yerel CLI'nin HomeGraph kabulünü bağımsız olarak ispatlamadığı anlamına gelir.
