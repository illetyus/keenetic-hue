# Günlük bakım — hızlı başvuru

Bu hızlı başvuru, birleşik Keenetic Hue kurulumu ve RC12 yönetim komutları içindir. Boş bir Entware ortamında önce [kurulum kılavuzunu](installation.md) izle.

Bu sayfadaki komutları Keenetic'e SSH ile bağlanıp etkileşimli terminalde çalıştır. Parolayı, kurulum kodunu veya token'ı komut satırına yazma; araç gizli giriş gerektiğinde kendisi sorar. Ayrıntılı işlem ve hata adımları ilgili kılavuzlarda bulunur.

## Sağlık ve durum

```sh
huectl status
huectl health --live --json
```

`health` çıktısındaki `healthy: true` sağlıklı yerel durumu, çıkış kodu `0` başarılı sorguyu gösterir. `status` ve `health` ampule yazma komutu göndermez. Google Home/HomeGraph kabulünü tek başlarına kanıtlamazlar.

## Google Home için yeni bağlama kodu

Yalnızca Google Home hesabını ilk kez bağlarken veya yeniden bağlarken gerekir:

```sh
huectl setup-code
```

Komut terminalde tek kullanımlık kodu ve `600` saniyelik geçerliliği gösterir. Kod bir kez kullanılabilir; ekrana gelen kodu Google Home'daki `[test] Keenetic Hue` sağlayıcısına gir. Komut TTY dışında çalışmayı reddeder; kodu dosyaya veya loga yönlendirme. Bu günlük işlem için AI veya MCP gerekmez. Ayrıntı: [Google Home ve kurulum kodu](google-home.md).

## Şifreli yedek oluşturma

```sh
mkdir -p /opt/var/lib/keenetic-hue-system/backups
chmod 700 /opt/var/lib/keenetic-hue-system/backups
huectl backup /opt/var/lib/keenetic-hue-system/backups/hue-20261102.huebak
```

Örnekteki tarihi yedeği aldığın günün tarihiyle değiştir. Komut parolayı gizli olarak iki kez sorar. Parola yöneticisinde sakla; `.huebak` dosyasını USB dışında da güvenli bir yerde tut. Yedek içeriğini, parolasını veya token'ları destek mesajlarında paylaşma. Ayrıntı: [şifreli yedek ve geri yükleme](backup-restore.md).

## Var olan kuruluma yedek geri yükleme

Önce doğrulama provası, ardından gerçek geri yükleme ve sağlık sorgusu:

```sh
huectl restore /path/to/backup.huebak --dry-run
huectl restore /path/to/backup.huebak
huectl health --live --json
```

Parola gizli terminal isteminde sorulur. Bu yol aynı cihaz düzeni içindir; boş Entware kurulumu veya USB kaybından dönüş için [yeniden kurulum kılavuzunu](reinstall.md) izle.

## İmzalı güncelleme ve geri alma

Güncellemeden önce yeni yedek al. İmzalı `.huerel` paketini SFTP ile `/opt/var/tmp/hue-install/` altına kopyala, `VERSION` kısmını gerçek dosya adıyla değiştir:

```sh
huectl health --live --json
huectl update /opt/var/tmp/hue-install/keenetic-hue-VERSION.huerel
huectl health --live --json
```

Yeni sürüm sağlığı kabul etmezse araç kayıtlı önceki sürüme döner. Elle önceki doğrulanmış sürümü seçmek için:

```sh
huectl rollback
huectl health --live --json
```

Yarım kalan yerel bakım işlemini inceleyip kurtarmak için:

```sh
huectl recover
huectl health --live --json
```

Ayrıntılı sürüm sırası, uyumluluk yolu ve hata açıklamaları: [güncelleme, onarım ve geri alma](update-rollback.md).

## Yeniden kurulum

Yalnızca yedek USB/Entware kaybından sonra [yeniden kurulum kılavuzunu](reinstall.md) kullan. Yeni USB'yi çalışan USB'den ayrı hazırlamak gerekir. Ampulü sıfırlama veya yeniden eşleştirme, aynı donanım ve geçerli özel yedekle yapılan normal yeniden kurulumun parçası değildir.
