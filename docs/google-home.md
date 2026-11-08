# Google Home hesabını bağlama ve kurulum kodu yönetimi

Worker sürüm 31’de canlı yeniden bağlama, cihaz senkronizasyonu ve ardından lamba komutu doğrulandı. Worker, bağlama sayfası için güvenli çereze bağlı 10 dakikalık imzalı form üretir. Kurulum kodu yalnız bir kez kullanılır. Başarılı yeniden bağlama önceki Google Home yetkisinin yerine geçer.

Bu bölüm mevcut Cloudflare/Google kurulumunun yönetimini anlatır. İlk kurulumda Worker, Durable Object, Tunnel/VPC Service ve Google Cloud-to-cloud projesi ayrıca hazırlanmış olmalıdır. Tam bulut kaybı için [Cloudflare/Google yeniden hazırlama kılavuzu](cloud-rebuild.md) vardır; bu akışın yeni hesap/projede canlı kabulü ayrıca yapılmalıdır. Aşağıdaki komut yalnız issuer secret'ını yönetir.

## Yeni kod üretmek

Keenetic SSH terminalinde:

```sh
huectl setup-code
```

Komut 26 karakterlik kodu ve 600 saniyelik geçerlilik bilgisini gösterir. Yeni kod önceki kullanılmamış kodu geçersiz kılar; bağlı Google hesabının erişimini tek başına kaldırmaz. Kod yalnız bir kez kullanılabilir. Çıkışı dosyaya yönlendirirsen komut kod üretmeden `SETUP_CODE_REQUIRES_TERMINAL` ve çıkış kodu 2 ile durur.

Telefonda aynı test kullanıcısının Google hesabıyla Google Home'da cihaz/hizmet ekleme ekranındaki Google ile çalışan hizmetler listesine gir. `[test] Keenetic Hue` sağlayıcısını seç. Açılan hizmet sayfasına yeni kodu girip bağlamayı onayla. Liste adı Google konsolundaki entegrasyon adına bağlıdır. Kodun süresi dolarsa SSH'den yenisini üret. Mevcut bağlantı düzgün çalışıyorsa günlük kullanım için yeni kod gerekmez.

Kodu, issuer anahtarını veya token dosyalarını destek mesajlarına yapıştırma. Normal yönetim için yapay zekâ, MCP veya Google şifresini bu servise verme gereksinimi yoktur.

## Issuer anahtarını ilk kez kurmak veya döndürmek

Bu yetki yalnız `/admin/setup-code` üzerinden kısa ömürlü kod üretir. Lamba kontrolü, hesap iptali ve HomeGraph yönetimi yapamaz. Cloudflare genel API anahtarı veya operator token'ı Keenetic'e kopyalanmaz.

PC'de proje kökünden, kilit dosyasındaki Wrangler sürümünü kur ve Cloudflare'ın kendi giriş akışını tamamla:

```sh
cd cloud
npm ci
node node_modules/wrangler/bin/wrangler.js login
cd ..
node tools/cloud-bootstrap.mjs generate-issuer
```

Son komut, yalnız açık terminalde 32 rastgele bayttan üretilmiş 64 küçük hex karakterini gösterir. Anahtarı parola yöneticisine kaydet. Terminal kaydı alan bir oturumda çalıştırma. Ardından:

```sh
node tools/cloud-bootstrap.mjs provision-issuer
```

Anahtarı gizli girişe yapıştır. Araç anahtarı Wrangler'a stdin üzerinden verir; argv'ye veya depo dosyasına yazmaz. Alt süreç çıktısı hata ayrıntılarıyla birlikte bastırılır. Başarılı sonuç `SETUP_ISSUER_TOKEN` güncellendi bilgisidir. İşlem `keenetic-hue-relay` Worker'ını hedefler; öncesinde doğru Cloudflare hesabında olduğunu denetle. Araç genel bir Worker/Tunnel/Google proje kurucusu değildir.

Sonra Keenetic SSH terminalinde **kendi Worker HTTPS origin adresini** kullan:

```sh
huectl configure-issuer --origin https://WORKER.HESAP.workers.dev
```

Aynı anahtarı gizli girişe yapıştır. HTTPS origin'e yol, sorgu, kullanıcı adı veya parola ekleme. Araç önce uzak kod üretme yetkisini doğrular; başarısız doğrulamada mevcut yerel anahtarı değiştirmez. Başarılı sonuç `configured: true` ve `authority: setup-code-only` içerir. Doğrulama sırasında üretilen kod gösterilmez; sonra `huectl setup-code` ile kullanacağın kodu üret.

Anahtar döndürülürken Cloudflare güncellemesi ile cihaz güncellemesi arasında kod üretimi geçici olarak çalışmayabilir; mevcut Google hesabı bağlantısı ve lamba kontrolü bu issuer anahtarına bağlı değildir. Elektrik kesilmesiyle yerel iki dosya güncellemesi yarım kalırsa aynı `configure-issuer` komutunu tekrar çalıştır. Çok dosyalı güncellemenin tek atomik işlem olduğu varsayılmaz.

Issuer anahtarını iptal etmek için yetkili Cloudflare oturumunda bu Worker'ın `SETUP_ISSUER_TOKEN` secret'ını kaldır veya yeni rastgele değerle değiştir. Bu işlem mevcut Google hesabının OAuth erişimini iptal etmez; hesap iptali ayrı operatör işlemidir.

## Yeniden kurulum sınırları

- Yalnız Entware/USB yenilenmişse cihaz yedeği ve aynı bulut kaynakları kullanılır. Boş kurulum geri yükleme yolu cihazdaki izole işlev provasını geçti; fiziksel USB'yi silerek yapılan bir prova iddia edilmez. [Yeniden kurulum kılavuzunu](reinstall.md) kullan.
- Worker/Tunnel kaybedilmişse Tunnel/VPC Service ve Worker binding'leri yeniden hazırlanır; Tunnel token'ı yenilenir. Yalnız issuer komutu bunu yapmaz.
- Google projesi/hizmet hesabı anahtarı kaybedilmişse Google konsolunda API, hizmet hesabı ve JSON anahtarı yeniden hazırlanıp Worker secret'ına yüklenir. Cloudflare secret değerlerinin geri okunabildiği varsayılmaz.
- Google veya Cloudflare'ın hukuki şartları kullanıcı tarafından kabul edilir; kurulum araçları bu kabulü otomatik yapmaz.

Bu bulut kurulum prosedürlerinin ayrıntıları [bulut kılavuzundadır](cloud-rebuild.md). Yeni hesap/projede baştan sona canlı kabul ayrı bir iştir; mevcut yönetim komutlarının çalışması bunun tamamlandığı anlamına gelmez.
