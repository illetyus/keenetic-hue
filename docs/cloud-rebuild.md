# Cloudflare/Google kaynaklarını yeniden hazırlama

Bu kılavuz yeni veya kaybolmuş bulut kaynaklarının hazırlanması içindir. Worker sürüm 31 mevcut kurulumda kabul edildi. Ayrı bir Google ve Cloudflare hesabında baştan sona kurulum henüz doğrulanmadı.

Bu belge tam bulut kaybı için operatör kılavuzudur. Mevcut çalışan kaynakları bu adımları prova etmek için silme. Yalnız USB/Entware değiştiyse [cihaz yedeğinden yeniden kurulum](reinstall.md) yeterli olabilir. Cihaz yedeği Cloudflare Durable Object veritabanını, Google projesini veya geri okunamayan Worker secret'larını içermez.

Bu akış belge ve mevcut uygulama kaynaklarıyla eşleştirildi; yeni bir Cloudflare hesabı ve yeni Google projesinde baştan sona canlı kabul yapılmış sayılmaz. Mevcut RC12 kurulumu hedefli inceleme ve canlı kabulden geçti; yeni hesaplarla ilk kurulum bunun kapsamına girmez.

## 1. Kurtarma senaryosunu belirle

| Kayıp | Gerekli işlem |
|---|---|
| Yalnız USB/Entware | Aynı adaptörle imzalı yazılım + özel yedek; önce yerel sağlık |
| Yalnız Tunnel | Yeni Tunnel ve VPC Service, cihazda yeni Tunnel token'ı, Worker binding güncellemesi |
| Worker kodu, DO'lar duruyor | Aynı Worker adı/binding/migration ve secret'larla yeniden deploy; DO namespace silinmez |
| DO verisi veya tüm Cloudflare hesabı | Yeni DO'lar/secret'lar; eski OAuth grant geri gelmez, Google hesabı yeniden bağlanır |
| Google proje/hizmet hesabı | Yeni API/entegrasyon/hizmet hesabı, Worker değişkenleri/secret'ları ve yeniden hesap bağlama |

## 2. PC araçları ve yapılandırma

Desteklenen Node.js ile proje kökünde:

```sh
cd cloud
npm ci
node node_modules/wrangler/bin/wrangler.js login
node node_modules/wrangler/bin/wrangler.js whoami
```

Doğru Cloudflare hesabını seç. `wrangler.tunnel.example.jsonc` dosyasını `wrangler.personal.jsonc` adına kopyala. Bu yerel dosyada VPC Service ID, Google proje ID'si, OAuth client ID ve tam Worker origin'ini kendi kaynaklarınla doldur. Secret değerlerini JSONC'ye yazma. Bağımlılık kilidini kullan; sırf yeniden kurulum için rastgele en yeni Wrangler sürümüne geçme.

## 3. Tunnel ve VPC Service

Cloudflare dashboard'da **Workers VPC → Tunnels → Create** ile Tunnel oluştur. Cihazdaki bu projeye ait doğrulanmış MIPS `cloudflared` ikilisini kullan; dashboard'daki başka mimariye ait indirme komutunu cihazda çalıştırma. Tunnel token'ını parola yöneticisinde sakla ve Keenetic'te yalnız root'un erişebildiği `secrets/tunnel.token` dosyasına güvenli biçimde aktar. SFTP ile hazırlanmış 0600 dosyayı sabit hedefe taşı; token'ı komut argümanı veya sohbet mesajı yapma. Sonra `huectl restart tunnel` çalıştır.

**VPC Services → Create** bölümünde bu Tunnel'ı seç. Uygulamamızın kaynaktaki HTTP hedefi `127.0.0.1`, portu `8788`'dir. VPC Service'i aynı HTTP hedefiyle oluştur ve verilen Service ID'yi `HUE_TUNNEL` binding'ine yaz. Bu, router'da gelen port veya özel alan adı gerektirmez. Workers VPC için ilgili hesapta Connectivity Directory yetkisi gerekir. Resmî adımlar: [Workers VPC başlangıç](https://developers.cloudflare.com/workers-vpc/get-started/), [özel HTTP API örneği](https://developers.cloudflare.com/workers-vpc/examples/private-api/).

## 4. Kimlik bilgilerini yerleştirme

Cloudflare Worker'ın **Settings → Variables and Secrets** bölümünde aşağıdakileri secret olarak sakla. Değerleri terminal çıktısına veya depoya koyma:

| Secret | Kaynak ve sınır |
|---|---|
| `DEVICE_TOKEN` | Cihazın `secrets/device.token` dosyasıyla aynı değer; Tunnel içindeki yerel komut yetkisi |
| `OPERATOR_TOKEN` | Bağımsız rastgele yönetim anahtarı; Keenetic'e kopyalanmaz |
| `SETUP_ISSUER_TOKEN` | Cihazın `secrets/setup-issuer.token` dosyasıyla aynı; yalnız kurulum kodu üretme |
| `OAUTH_CLIENT_SECRET` | Google Home entegrasyonundaki OAuth client secret ile aynı |
| `HOMEGRAPH_SERVICE_ACCOUNT` | Google hizmet hesabının JSON anahtarının tamamı; cihazda saklanmaz |

Yalnız USB yedeğindeki geçerli cihaz/issuer anahtarlarını kullanacaksan bunları yeniden üretmek yerine aynı değerleri koru. Token daha önce iptal edilmişse eski dosyayı geri kopyalamak yetkiyi geri getirmez. Yeni anahtar seçtiğinde iki uç birlikte güncellenmelidir. Yerel `secrets/api.token`, Worker'a yüklenmez.

Issuer anahtarı için gizli terminal girişi kullanan mevcut araç da vardır:

```sh
cd ..
node tools/cloud-bootstrap.mjs generate-issuer
node tools/cloud-bootstrap.mjs provision-issuer
```

Bu araç yalnız `keenetic-hue-relay` adlı mevcut Worker'ın `SETUP_ISSUER_TOKEN` secret'ını yönetir. Genel Worker/Tunnel/Google kurucusu değildir. Cihaz eşlemesini `huectl configure-issuer --origin https://WORKER.HESAP.workers.dev` ile tamamla; anahtarı gizli girişte ver. Doğrulama başarısızsa önceki yerel anahtar korunur.

## 5. Worker ve kalıcı veritabanları

Örnek yapılandırmada uygulamaya ait `HUE_RELAY`/`Relay` ve `HUE_AUTH`/`AuthStore` Durable Object binding'lerini ve migration tag'lerini koru. Yeni namespace ilk oluşturulduğunda Google OAuth bağlantıları boş olacaktır. Mevcut namespace'i silmek kod geri alma yöntemi değildir.

`PUBLIC_ORIGIN` tam HTTPS Worker origin'i, `GOOGLE_PROJECT_ID` gerçek Google proje ID'si ve `OAUTH_CLIENT_ID` entegrasyonda belirlediğin client ID olmalıdır. Kuyruk ve raporlama bayraklarını örnekteki profil ile eşleştir. Kodun yüklenmesi bütün secret'ların doğru olduğunu tek başına ispatlamaz.

PC'de `cloud` klasöründe:

```sh
node node_modules/wrangler/bin/wrangler.js deploy --dry-run --config wrangler.personal.jsonc
node node_modules/wrangler/bin/wrangler.js deploy --config wrangler.personal.jsonc
```

Deploy komutunu yalnız kendi kurtarma işlemin sırasında çalıştır. Mevcut kurulumda yeni bir bulut deploy'u bu belgeyi doğrulamak için yapılmadı. `https://WORKER.HESAP.workers.dev/health` yanıtı yalnız Worker erişimini gösterir; gerçek ampul sağlığını göstermez. NextDNS gibi bir filtre kullanılıyorsa seçtiğin tam Worker origin'inin erişilebilir olduğunu kontrol et.

## 6. Google Home projesi

[Google Home Developer Console](https://console.home.google.com/) içinde kendi hesabınla projeyi oluştur/seç ve Cloud-to-cloud entegrasyonunu hazırla. Şartların kabulünü kullanıcı kendisi yapar. Entegrasyonda `LIGHT`, aç/kapat ve parlaklık desteğini kullan. OAuth authorization-code akışındaki alanlar bu uygulamada şöyledir:

| Alan | Değer |
|---|---|
| OAuth client ID | Worker `OAUTH_CLIENT_ID` ile aynı |
| OAuth client secret | Worker `OAUTH_CLIENT_SECRET` ile aynı |
| Authorization URL | `https://WORKER.HESAP.workers.dev/oauth/authorize` |
| Token URL | `https://WORKER.HESAP.workers.dev/oauth/token` |
| Fulfillment URL | `https://WORKER.HESAP.workers.dev/fulfillment` |

Google projesinin gerçek ID'si Worker'daki ID ile eşleşmelidir; OAuth dönüş adresi bu proje kimliğine bağlı doğrulanır. Rastgele redirect URI ekleme. Kişisel geliştirme için entegrasyonun test durumunu ve telefondaki Google hesabının test erişimini etkinleştir. Genel sertifikasyon/yayın bu kişisel kurulumun yerine geçmiş sayılmaz.

## 7. HomeGraph durum raporlama

Aynı Google Cloud projesinde HomeGraph API'yi etkinleştir. Resmî yönergeye göre bir hizmet hesabı ve JSON anahtarı oluştur; güncel yönergede rol **Service Accounts → Service Account OpenID Connect Identity Token Creator** olarak belirtilir. JSON'u `HOMEGRAPH_SERVICE_ACCOUNT` secret'ına yükle. Anahtarı depoya veya cihaz yedeğine koyma. [Google'ın API ve anahtar adımları](https://developers.home.google.com/cloud-to-cloud/integration/report-state#get_started) ve [Request Sync](https://developers.home.google.com/cloud-to-cloud/integration/request-sync) belgelerini kullan.

`REPORT_STATE_ENABLED` etkin olmalıdır. Uygulama JSON anahtarla HomeGraph scope'u için token üretir; cihaz Google şifresini almaz. Yalnız yerel BLE/Tunnel sağlığı HomeGraph'ın son raporu kabul ettiğini kanıtlamaz; bir Google Home komutu ve rapor kabulü ayrıca kontrol edilir.

### Hizmet hesabı anahtarının ömrü ve yenilenmesi

Anahtarın durumunu ve tarihini **IAM & Admin → Service Accounts → ilgili hesap → Keys** ekranından kontrol et. Google, hizmet hesabı anahtarlarının en az 90 günde bir döndürülmesini önerir ve kaçırılmış bir döndürmenin kesintiye yol açmaması için anahtarı otomatik sona erme tarihine bağlamayı üretim anahtarları için önermez. [Google anahtar döndürme yönergesi](https://docs.cloud.google.com/iam/docs/key-rotation) · [Hizmet hesabı anahtarları ve sona erme](https://docs.cloud.google.com/iam/docs/service-account-creds#key-expiry).

Yenileme sırası:

1. Aynı hizmet hesabı için yeni JSON anahtarı oluştur. İndirdiğin JSON'u yalnızca parola yöneticisi veya şifreli, erişimi kısıtlı yerel kasada sakla; depoya, Keenetic'e, USB cihaz yedeğine veya destek mesajına koyma.
2. Cloudflare'da `keenetic-hue-relay` Worker'ının `HOMEGRAPH_SERVICE_ACCOUNT` secret'ını yeni JSON ile değiştir ve yeni Worker sürümünü deploy et. Wrangler kullanılıyorsa depo kökünden önce `cd cloud`, sonra sabitlenmiş yerel araçla `node node_modules/wrangler/bin/wrangler.js secret put HOMEGRAPH_SERVICE_ACCOUNT --config wrangler.personal.jsonc` çalıştır. Komut secret değerini etkileşimli olarak alır ve deploy eder; komut geçmişine JSON içeriği yazma. Kendi hesabına ait, Git dışında tutulan aynı kişisel yapılandırmayı kullan. [Cloudflare Worker secrets](https://developers.cloudflare.com/workers/configuration/secrets/).
3. Secret güncellemesi Worker sürümünü dağıtır, ancak HomeGraph nesnesi bir Durable Object içinde tutulur. Bu nesne, OAuth erişim belirtecini bellekte önbelleğe alır. Proje yapılandırmasında Durable Object kod güncelleme stratejisi açıkça ayarlanmamış; Cloudflare'ın 2026-10-30 tarihli güncel varsayılanı `deferred` ve etkin nesne yeni sürüme geçmeden önce en fazla 300 saniye eski kodla çalışabilir. Deploy tamamlandıktan sonra en az 6 dakika bekle; böylece yeni sürümün `AuthStore` nesnesini yeniden başlatması ve yeni secret'ı okuması için varsayılan bekleme aralığı aşılır. [Cloudflare Durable Object yaşam döngüsü](https://developers.cloudflare.com/durable-objects/concepts/durable-object-lifecycle/) · [2026-10-30 varsayılan değişikliği](https://developers.cloudflare.com/changelog/post/2026-10-30-durable-objects-deferred-code-updates/).
4. Beklemeden sonra `/admin/homegraph-check` çağır. `AuthStore` yeni sürümde yeniden başladıktan sonra ilk HomeGraph kimlik doğrulaması önbellekte eski bir belirteç olmadan gerçekleşir; `authenticated: true` yeni secret'taki anahtarın taze bir Google belirteci üretebildiğini doğrular. Ardından `/admin/report-check` ile rapor kabulünü ve Google'dan okunan durum eşleşmesini denetle. İki uç da `OPERATOR_TOKEN` ister. Bu yönetim anahtarı Keenetic'e yüklenmez ve Cloudflare daha sonra gizli değerini göstermez; oluştururken parola yöneticisine kaydet. Anahtara artık erişimin yoksa parola yöneticisinde en az 32 karakterlik rastgele yeni bir `OPERATOR_TOKEN` üret, Worker secret'ını güncelle, Save/Deploy adımını tamamla ve güvenli kopyasını sakla. Değeri komut satırı geçmişine veya sohbete koyma. Lamba kontrolünü ve otomatik raporu Google Home'da doğrula.
5. Yeni anahtarın taze belirteç ürettiği ve raporun eşleştiği doğrulanana kadar eski Google anahtarını etkin bırak. Eski anahtarı **Google Cloud Console'dan devre dışı bırakamazsın**; Cloud Shell'de `gcloud iam service-accounts keys list --iam-account=YOUR_HOMEGRAPH_SERVICE_ACCOUNT --managed-by=user` ile eski ve yeni kullanıcı anahtarlarının kimliklerini kontrol et. Sonra eski anahtarı `gcloud iam service-accounts keys disable ESKI_KEY_ID --iam-account=YOUR_HOMEGRAPH_SERVICE_ACCOUNT --project=YOUR_GOOGLE_PROJECT_ID` ile devre dışı bırak. Google, devre dışı bırakmanın önceden verilmiş kısa ömürlü erişim belirteçlerini iptal etmediğini belirtir; bu nedenle son kontrolü belirteç süresi dolduktan sonra (en fazla 60 dakika) tekrarla. Sorun çıkarsa eski anahtarı `gcloud iam service-accounts keys enable ESKI_KEY_ID --iam-account=YOUR_HOMEGRAPH_SERVICE_ACCOUNT --project=YOUR_GOOGLE_PROJECT_ID` ile yeniden etkinleştir, eski JSON'u Worker secret'ına geri yükleyip deploy et ve Durable Object geçiş penceresini bekle. İyileşmenin ardından eski anahtar artık gerekmiyorsa `gcloud iam service-accounts keys delete ESKI_KEY_ID --iam-account=YOUR_HOMEGRAPH_SERVICE_ACCOUNT --project=YOUR_GOOGLE_PROJECT_ID` ile sil. Google, silmeden önce anahtarı devre dışı bırakıp artık gerekmediğine emin olmayı önerir. [Google anahtarları listeleme ve devre dışı bırakma](https://docs.cloud.google.com/iam/docs/keys-disable-enable) · [Google anahtar silme](https://docs.cloud.google.com/iam/docs/keys-create-delete).
6. Sonraki döndürme tarihini 90 günlük bakım takvimine yaz. Cloudflare secret değerlerini sonradan göstermez; JSON'un güvenli özgün kopyasını saklamazsan aynı anahtarı geri yükleyemezsin. Cloudflare secret güncellemesi yeni Worker sürümünün dağıtımını gerektirir; dashboard'da Save/Deploy adımını tamamla. Bu proje Worker sürümlerini kademeli dağıtıma alırsa, yeni sürüm yüzde 100'e ulaşmadan ve Durable Object yeni sürümü benimsemeden anahtar döndürme doğrulamasını tamamlanmış sayma.

Yönetim uç noktalarını güvenilir Windows PowerShell 7 oturumundan çağır. Aşağıdaki kod token'ı gizli istemde alır; token'ı komut satırı argümanına veya komut geçmişine koymaz. İstek sırasında token işlem belleğinde kullanılır, bu yüzden kodu yalnız kendi güvendiğin bilgisayarda çalıştır. `WORKER.HESAP.workers.dev` kısmını Cloudflare'daki gerçek Worker adresiyle değiştir.

```powershell
$origin = "https://WORKER.HESAP.workers.dev"
$secureToken = Read-Host "OPERATOR_TOKEN" -AsSecureString
$tokenPointer = [IntPtr]::Zero
$operatorToken = $null
$headers = $null
$oldErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Stop"
try {
    $tokenPointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $operatorToken = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    $headers = @{ Authorization = "Bearer $operatorToken" }
    $auth = Invoke-RestMethod -Method Post -Uri "$origin/admin/homegraph-check" -Headers $headers -TimeoutSec 15
    if (-not $auth.authenticated) { throw "HOMEGRAPH_AUTH_NOT_CONFIRMED" }
    $report = Invoke-RestMethod -Method Post -Uri "$origin/admin/report-check" -Headers $headers -TimeoutSec 15
    if (-not $report.reported -or -not $report.matched) { throw "HOMEGRAPH_REPORT_NOT_CONFIRMED" }
    [pscustomobject]@{
        authenticated = $auth.authenticated
        reported = $report.reported
        matched = $report.matched
        lamp = $report.state
    } | ConvertTo-Json -Depth 4
}
finally {
    if ($tokenPointer -ne [IntPtr]::Zero) {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    }
    if ($secureToken) { $secureToken.Dispose() }
    $operatorToken = $null
    $headers = $null
    $ErrorActionPreference = $oldErrorActionPreference
}
```

Başarıda `authenticated`, `reported` ve `matched` alanları `true` olur; çevrimdışı lambada rapor kontrolü eşleşme vermez. Bu sorgu fiziksel ampule yazma komutu göndermez. PowerShell'de hata oluşursa hata kodu/değişken içeriği yerine yalnız genel hata türünü kaydet; token'ı hiçbir zaman paylaşma.

## 8. Kabul sırası

1. Keenetic'te `huectl health --live --json`: doğru sürüm, doğrulanmış BLE, hazır Tunnel.
2. Worker `/health`: erişilebilir HTTPS origin.
3. `huectl setup-code`: yeni tek kullanımlık kod.
4. Google Home'da aynı test hesabıyla `[test] Keenetic Hue` hizmetini bağlama.
5. Tek aç/kapat ve parlaklık denemesi, sonra mobil internet üzerinden tekrar.
6. Komut kabulü ile HomeGraph rapor kabulünü ayrı doğrulama; bir hata varsa anahtar/JSON içeriğini paylaşmadan ilgili bileşenin durumunu inceleme.

Tam bulut kaybında eski Google hesabı grant'inin geri geldiği varsayılmaz. Aynı USB yedeğiyle yerel cihazı ayağa kaldırmak, yeni bulut kaynaklarının kabul testini tamamlamaz.
