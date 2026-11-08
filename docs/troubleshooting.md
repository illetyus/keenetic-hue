# Sorun giderme

Bu komutlar birleşik Keenetic Hue kurulumundaki RC12 bakım yolu içindir.

Komutları Keenetic'e SSH ile bağlanıp etkileşimli terminalde çalıştır. Çıktıları paylaşman gerekirse kurulum kodunu, parolayı, token'ı, bond dosyasını veya yedeği ekleme. Sorun giderirken Hue ampulünü fabrika ayarına döndürme, BLE eşleştirmesini silme veya çalışan Entware USB'sini biçimlendirme.

## SSH sunucu anahtarı değişmiş uyarısı

`REMOTE HOST IDENTIFICATION HAS CHANGED` veya Termius'ta sunucu anahtarı değişti uyarısı görülürse oturumu durdur. Bu, modem yeniden kurulmuş ya da sunucu anahtarı bilinçli değiştirilmiş olabileceği gibi yanlış cihaza bağlanma veya araya girme riskini de gösterebilir. Uyarının kendisinde görünen parmak izini tek başına doğrulama sayma.

Parmak izini daha önce güvenilir biçimde kaydedilmiş değerle veya SSH bağlantısından bağımsız güvenilir yerel Keenetic yönetim oturumuyla karşılaştır. Yalnız aynı modem/port için, değişikliğin beklenen olduğu bağımsız doğrulanınca kayıtlı eski anahtarı yenile. Doğrulama yoksa eski `known_hosts`/Termius kaydını silme, `StrictHostKeyChecking` kapatma ve parola ya da anahtar göndermeye devam etme. `ssh-keyscan` ile alınan anahtar tek başına kimlik doğrulamaz.

## Önce Entware USB'sini ve `huectl`'ü kontrol et

Entware USB'si Keenetic'te takılı ve `/opt` bağlanmış olmalı. USB'yi bilgisayara aldıysan yeniden biçimlendirmeden Keenetic'e tak; mevcut dosyaları silme. SSH terminalinde:

```sh
df -h /opt
test -x /opt/bin/huectl && echo HUECTL_OK
```

`HUECTL_OK` görünmüyorsa `/opt` bağlanmamış olabilir. USB'nin Keenetic'e fiziksel olarak takılı olduğunu ve Keenetic'in harici depolama/Entware alanında göründüğünü kontrol et. Bu aşamada kurulum komutlarını tekrar çalıştırma; yanlış hedefe kurulum mevcut dosyaları veya çalışan hizmeti bozabilir. USB kaybı ya da yeni USB kullanımı için [yeniden kurulum kılavuzunu](reinstall.md) izle.

## Ajan, BLE ve Tunnel durumunu ayır

```sh
huectl status
huectl health --live --json
```

`status` servis süreçlerinin çalışıp çalışmadığını gösterir; tek başına BLE bağlantısını veya ampul yanıtını doğrulamaz. `health --live --json` yerel API'yi, ampule yapılan güncel BLE okumasını ve Tunnel hazır yanıtını sorgular; ampule yazma komutu göndermez. Başarılı sağlık sorgusu `healthy: true` ve çıkış kodu `0` verir. Degraded/stopped durumunda sağlık sorgusu çıkış kodu `1` verir. Bilinmeyen komut, hatalı seçenek veya etkileşimli terminal gerektiren komut için genellikle `2` döner.

JSON içindeki `components` alanlarına göre ilerle:

- `manager` veya `agent` `stopped` ise önce Entware USB'sinin bağlı ve `/opt`'un kullanılabilir olduğunu doğrula. Ardından `huectl status` ile sahip süreçleri tekrar kontrol et.
- `localApi` `degraded` ya da `LOCAL_CHECK_FAILED` ise ajan yerel sağlık isteğine yanıt vermemiştir. USB ve `agent` durumunu kontrol et; tekrar ederse tanı çıktısını özel değerler olmadan kaydet.
- `ble` `degraded` ya da `BLE_UNAVAILABLE` ise ampulün fiziksel anahtarının açık ve Bluetooth adaptörünün Keenetic'e takılı olduğunu kontrol et. Bir kez `huectl health --live --json` ile yeniden oku. Servis ampul kapalı diye yeniden başlatılmaz; eşleştirme/tarama araçlarını ajan çalışırken kullanma.
- `tunnel` `degraded`, `TUNNEL_UNREACHABLE` veya hazır değilse Keenetic'in WAN, DNS ve saat durumunu kontrol et. Lamba/agent çalışıyorsa yalnız Tunnel'ı yeniden başlat:

  ```sh
  huectl restart tunnel
  huectl health --live --json
  ```

- `reporting` `unknown` / `NOT_CHECKED` görünmesi yerel sağlık sorgusunun HomeGraph rapor kabulünü ölçmediği anlamına gelir; tek başına raporlama arızası kanıtı değildir. Google Home çevrimdışı kalıyorsa yerel bileşenleri Tunnel ve Google bağlantısından ayrı incele. [Bulut bağlantısı kılavuzuna](cloud-link-operations.md) bak.

Yalnızca `agent` durmuş veya BLE kontrolü tekrar tekrar başarısız olmuşsa, USB, adaptör ve ampul gücünü doğruladıktan sonra ajanı yeniden başlat:

```sh
huectl restart agent
huectl health --live --json
```

Servis yönetimi çıkış kodu `1` verirse (mesaj yazmadan dönebilir) veya `SERVICE_ACTION_FAILED` görünürse başka süreçleri elle öldürme; önce `huectl status` çıktısını ve `huectl diagnose --json` sonucunu incele. Tanı çıktısını paylaşmadan önce kişisel veya gizli bilgileri çıkar.

## Başlangıçta SQLite kurtarma hatası ve yaşam döngüsü kayıtları

Bu tanılar, yaşam döngüsü kayıtlarını içeren sürüm kurulduktan sonra kullanılabilir.
`SERVICE_ACTION_FAILED component=agent stage=checkpoint reason=AGENT_DATABASE_SNAPSHOT_FAILED:SCHEMA`
veya sağlık çıktısındaki `CHECKPOINT:AGENT_DATABASE_SNAPSHOT_FAILED:SCHEMA`, ajanın
başlangıç öncesi veritabanı kontrolünde durduğunu gösterir. Bu kod tek başına
veritabanının bozuk olduğunu kanıtlamaz: yarım kalmış SQLite işleminin sıcak
rollback journal dosyası, salt okunur açılışta aynı hataya yol açabilir.

Birlikte başlangıçta (`huectl start` veya `huectl restart`) ajan başarılı olmadan
Tunnel başlatılmaz. Bu kasıtlı bağımlılıkta Tunnel kaydı
`DEPENDENCY:AGENT_START_FAILED` gösterir; asıl ajan aşamasını ayrıca incele.
`huectl start tunnel` ile bağımlılığı atlamak SQLite sorununu onarmaz.

Entware SSH terminalinde salt okunur tanı komutları:

```sh
huectl status
huectl health --json
tail -n 20 /opt/var/lib/keenetic-hue-system/logs/lifecycle.log
tail -n 20 /opt/var/lib/keenetic-hue-system/logs/agent-supervisor.log
tail -n 20 /opt/var/lib/keenetic-hue-system/logs/tunnel-supervisor.log
```

Yönetici olayları `logs/lifecycle.log`, supervisor olayları bileşene özel
`logs/*-supervisor.log` içinde tutulur. Her dosya yaklaşık 64 KiB ile sınırlanır;
bir önceki dosya `.previous` olarak saklanır. Kayıtlar root'a özeldir ve bileşen,
boot kimliği, PID/başlangıç kimliği, sürüm, UTC/monotonik zaman, aşama ve izinli
hata kodunu içerir. Komut içeriği, anahtar, token ve ham istisna metni yazılmaz.
Günlük yolu geçersizse veya yazılamıyorsa tanı atlanır; hizmetin sahiplik
denetimleri ve başlatma/durdurma kararı değiştirilmez. Böyle bir durumda kayıt
yokluğu, olayın gerçekleşmediğinin kanıtı değildir.

`SUPERVISOR_TERM` / `SUPERVISOR_INT`, aynı boot/PID/başlangıç/sürüm kimliğine ait
yakalanmış sinyal kaydını belirtir. `SUPERVISOR_EXIT_UNKNOWN`, eşleşen kapanış
kaydının olmadığını söyler; SIGKILL, güç kaybı veya USB arızası sonucu çıkarılamaz.
Çocuk sürecin çıkış kodu supervisor'ın neden kaybolduğunu açıklamaz.

Hata halinde veritabanını ve `-journal` eşini birlikte koru; journal'ı elle
silme, veritabanını tek başına değiştirme ve root altında rastgele SQLite
onarımı çalıştırma. Desteklenen kurtarma, ajan duruyken özgün çifti özel alanda
korur, geçici kopyayı yetkisiz ajan kimliğiyle kurtarır ve doğrular; belirsiz
durumdaki fiziksel komutları yeniden oynatmaz. İlk supervisor kaybının tetikleyicisi
önceki olayda kaydedilmediği için bilinmiyor; yeni kayıtlar geçmiş nedeni
geriye dönük kanıtlamaz. Onaylanmış imzalı sürüm ve geri dönüş sınırı için
[güncelleme kılavuzunu](update-rollback.md) izle.

## Google Home'da çevrimdışı görünme veya komut hatası

Önce yerel `huectl health --live --json` sonucunu kontrol et. Yerel sağlık iyi olsa bile bu, Google Home/HomeGraph'ın çevrimiçi durumunu kanıtlamaz. İlişkiyi kaldırıp yeniden bağlamak ilk adım değildir; yalnız Google hesabı gerçekten bağlantısını kaybettiyse [Google Home kılavuzundaki](google-home.md) yeniden bağlama adımlarını kullan.

Hızlı parlaklık değişikliklerinde son istek uygulanabilir; `PENDING` kabul edildiğini, ampulün işlemi tamamladığını değil, belirtir. Arka arkaya tekrarlamak yerine son değişikliğin uygulanmasını bekle ve ardından yeni canlı okuma yap. Beklenmeyen gerçek hata sürerse bir sonraki isteği göndermeden önce lamba ve servis durumunu doğrula.

## Kurulum kodu, yedek ve güncelleme hataları

- `SETUP_CODE_REQUIRES_TERMINAL`: `huectl setup-code` doğrudan etkileşimli SSH terminalinde çalıştırılmalı; çıktıyı dosyaya veya loga yönlendirme.
- `SETUP_CODE_UNAVAILABLE`: kurulum kodu üretici anahtarı sağlanamıyor veya HTTPS bağlantısı başarısız. Sırları paylaşmadan önce Entware/WAN/DNS ve Google bağlantısı durumunu kontrol et. Mevcut Google bağlantısını sırf yeni kod gerektiği için kaldırma.
- `PRIVATE_TERMINAL_REQUIRED`: yedekleme ve geri yükleme parolaları için komutu doğrudan SSH terminalinde çalıştır; parola pipe, dosya veya komut satırına yazılmaz.
- `BACKUP_OR_RESTORE_FAILED`: genel hata mesajı ayrıntıyı bilerek gizler. Dosya yolunu, boş alanı, girilen parolayı ve yedekleme/geri yükleme kılavuzundaki aynı-cihaz koşulunu kontrol et. Yedek dosyasını destek mesajına ekleme.
- Geri yükleme yarıda kesildiyse yalnız o işlem için `huectl restore --recover` çalıştır, ardından canlı sağlık kontrolü yap. `huectl recover` imzalı sürüm güncellemesi/onarımı gibi kesilmiş bakım işlemlerini kurtarır; yedek geri yükleme kurtarma komutunun yerine geçmez.
- `RELEASE_OPERATION_FAILED` veya `RELEASE_OPERATION_INTERRUPTED`: imza, uyumluluk ve bakım makbuzu durumunu kontrol et; gerekiyorsa [güncelleme ve geri alma kılavuzunu](update-rollback.md) izle. İmza kontrolünü kapatma veya rastgele arşiv kurma.

Parola, kod veya token değerini hiçbir hata çıktısına ekleme. Giderilemeyen durumda `huectl status`, `huectl health --live --json` ve ilgili genel hata kodunu paylaş; şifreli yedek, `keystore.json`, özel anahtar ya da tam token dosyası paylaşma.
