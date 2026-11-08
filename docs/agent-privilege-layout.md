# Hue ajanı için yetki ve veri düzeni

Bu belge RC5–RC7 dönemindeki yetki ayrımı tasarımını ve denemeleri kaydeder. Güncel RC12 işletim adımları için [günlük bakım](maintenance-quickstart.md) ve [kurulum](installation.md) kılavuzlarını kullan.

## Doğrulanan cihaz desteği

2026-10-30 canlı ve geri alınan kısa denemede yalnız Belkin050d:065a düğümü
root:61001/0660 yapıldı. Gerçek ajan kodu UID/GID61001, ek gruplar boş,
capability kümeleri sıfır ve no_new_privs1 altında ampule bağlandı, şifrelemeyi
başlattı, üretici/modeli doğruladı ve güç/parlaklık durumunu okudu. Kontrol
yazımı, yeni eşleştirme veya reset yapılmadı. Ardından düğümün eski root:root0640
izni, önceki çalışan ajan ve ana bond dosyasının özeti doğrulandı. Bağımsız
kurtarma gözcüsü denemeyi korudu ve başarıdan sonra kapatıldı.

Bu kanıt sürekli hotplug veya kalıcı ajan göçü kanıtı değildir. Fiziksel yeniden
takma ve son sistem yeniden başlatma kabulü ayrıca gereklidir.

2026-11-01 kontrollü RC5→RC6→RC5 canlı denemesinde imzalı RC6 gerçekten seçildi.
Ajan UID/GID61001 ve Tunnel61000, boş ek gruplar ve sıfır capability kümeleri
doğrulandı; seri numarasıyla sabit adaptörden şifreli durum okundu. Yönetilen
rollback RC5'e döndü; lamba durumu ve master/kimlik dosyası özetleri değişmedi,
sürüm sayacı6 korundu. Bağımsız gözcü son RC5 sağlık doğrulamasından sonra kapandı.
Lamba kontrol yazımı yapılmadı. Bu, fiziksel replug/reboot veya kalıcı kabul
kanıtı değildir; aralıklı snapshot zaman aşımı ayrıca açık kalmaktadır.

## Uygulama kararları

Ruling: Ana kök700 ve root master ayarları korunur. Ajan için dış runtime
üretilir; ana kökü gruba veya herkese açmak aynı güvenlik hedefini sağlamaz.

`agent_storage.agent_runtime_paths()` sabit yolları üretir:

| Alan | Hedef kullanım |
| --- | --- |
| `...-runtime/agent/code/<sürüm>` | İmzalı manifestten doğrulanan, root'un yazabildiği Python kodu |
| `...-runtime/agent/config` | Root'un ürettiği ayar ve yalnız ajan grubunun okuyabildiği gerekli iki API anahtarı |
| `...-runtime/agent/state` | Ajanın kendi bond ve komut veritabanı |
| `...-runtime/agent/run` | Ajanın PID/USB kilidi/özel sağlık soketi |
| `...-runtime/agent/logs` | Ajanın dönen günlükleri |

Ruling: Bumble JsonKeyStore geçici dosya + os.replace ile kaydeder. Bu nedenle
bond parent dizininin ajan tarafından yazılabilmesi gerekir. Bunu, ana kökü
açmak veya atomik kaydı kaldırmakla çözmeyeceğiz. Dış runtime'ın üst dizinleri
root tarafından sabitlenmeli; ajan kendi veri dizininin üstteki adını değiştirememeli.

Ruling: Ajan verisini root okurken dosya sahibini, grubunu, özel modunu, normal
dosya türünü, tek hardlink olmasını ve boyutunu açılan FD üzerinden denetler.
Denetim veriden önce yapılır. `read_agent_private()` bunun ilk uygulanmış
parçasıdır; keyfi kullanıcı yolu kabul eden ayrıcalıklı bir API olmayacaktır.

Ruling: Ajanın kontrol ettiği SQLite dosyası root içinde ayrıştırılmayacak.
`snapshot_agent_database()` ayrıştırmayı ajan UID'si altında yapar. İşçi yalnız
boş, root'un açtığı bir çıktı FD'sini miras alır; başka root dosyaları aktarılmaz.
Çıktı16MiB, CPU12s, adres alanı64MiB ve duvar süresi20s ile sınırlıdır. Kesin
komut/sequence şeması ve quick_check doğrulanır. Bu sınırlar gerçek cihazdaki
sahte veritabanı testlerini geçmiştir. Yeni ayrı kullanıcı sözleşmesinde yedek,
mevcut kurulum restore ve temiz kurulum restore akışları bu sınırı kullanır.
Eski imzalı sürümlerin root düzeni ayrı uyumluluk sözleşmesidir.

## Sağlık ve süreç kimliği

`authenticated_probe()` artık beklenen UID/GID'yi açıkça alabilir. Özel soket
ve parent dizin sahibi/modu, SO_PEERCRED PID/UID/GID ve çağıranın sahiplik
denetimi birlikte kullanılır. TCP yolunda kabul edilen bağlantının FD'si ve
beklenen gerçek/etkin/saved/filesystem UID/GID değerleri doğrulanır. Yanlış UID,
GID, PID ve sahiplik denetiminde token yüklenmeden ret, gerçek cihazdaki sahte
sunucuda doğrulandı.

Ruling: Beklenen kimlik istemciden değil, root'un koruduğu ve doğruladığı sürüm
sözleşmesinden ve kimlik profilinden alınır. Bootstrap/supervisor boot ve
startTicks denetimleri kaldırılmaz. Yeni ajan sürümü için child PID'nin startTicks
değeri de root tarafından kaydedilmeli; eski imzalı slotlara geri dönüş ayrı
doğrulanan sözleşmeyle yürütülmelidir.

## Veri yaşam döngüsü ve kabul durumu

### 2026-11-01 RC6 inceleme adayı (henüz dağıtılmadı)

Yeni builder imzalı `metadata/runtime-policy.json` üretir. Yeni ajan için ayrı
kullanıcı, iki bileşen için ayrıcalık kısıtlaması ve süreç kimliği sözleşmesi
zorunludur; eski imzalı RC4/RC5 geri dönüş sözleşmeleri ayrı tutulur. Root
başlatıcı PID/başlama tick'i/boot kimliği/sürüm kaydını UID düşürmeden önce
yazar; supervisor çıkışta temizler ve yönetici kaydı eşleştirir. Yedek/restore
bağlantısının native sahte servis kabulü geçti; kalıcı dağıtım, gerçek USB ve
kod inceleme kapıları ayrıca tamamlanmalıdır.

Başlatıcı ajan için dar USB hazırlığını, doğrulanmış kod/ayar projection'ını
ve UID/GID düşürmeyi sırayla çağırır. Başarısız UID düşürme root exec'e dönmez.
Python `-s -P -u -m` ile başlatılır: kullanıcı site-packages ve çalışma
dizininden modül eklenmesi kapatılır; yalnız sabitlenmiş PYTHONPATH kullanılır.
Bu seçeneklerin davranışı [Python 3.13 komut satırı belgesinden](https://docs.python.org/3.13/using/cmdline.html)
kontrol edildi. Gerçek UID61001 geçici testinde boş CWD arama girdisi olmadığı
da denetlendi. Başlatıcının yeni dalı kontrollü canlı RC6 denemesinde çalıştı;
son durumda RC5'e geri dönüldü.

Sağlık kontrolü, imzalı politikanın seçtiği ajan UID/GID'si ve runtime soketini
kullanır. Sadece servis hesabının mevcut olması eski root ajanının sağlık
kontrolünü değiştirmez. Yönetici yeni ajan için argv, ortam, dört UID/GID,
sıfır capability ve boş ek grupları doğrular. Durdurma ve yarım kalmış durdurma
sonrasındaki başlatma, kayıt silinmeden önce checkpoint ve USB geri alma
yardımcılarını çağırır. Checkpoint hatası sahiplik kaydını korur. Bu bağlantı
gerçek ayrı UID kullanan sahte ajan süreciyle uçtan uca test edildi; gerçek
USB/Tunnel bileşenleri bu fixture'da sentetiktir.

İlk aktarım PREPARED durumunda kesilirse, eski sürümün geri dönüşten sonra
ürettiği güncel canonical kayıtlar yeni denemede alınır; kaynak hash'leri
iki dosyadan önce kalıcılaştırılır. READY kaybında eski master verisinden
kendiliğinden oluşturma hâlâ yasaktır. Bu ayrım native geçici testlerde geçti.

Ruling: Ajan durduktan sonraki checkpoint, iki canonical dosya kalıcılaştıktan
sonra EXPORTED durumuna geçer. Bu andan itibaren eski sürüm canonical veriyi
güncelleyebilir. Aynı checkpoint'i tekrar çağırmak eski runtime verisini yeni
canonical verinin üstüne yazmaz. Yeniden yükseltme güncel canonical kayıtları
alır. İki dosyanın arasında kesinti READY durumunu korur; yeniden deneme
runtime'dan tamamlanır. PREPARED/EXPORTED aktarımı ile aktif READY kaybını
ayrı tutmak, eski komut günlüğünün sessizce geri yüklenmesini önler.

Native fixture'larda doğrulananlar: root:ajan0440 dosya denetimi, READY verisinin
yeniden başlatmada korunması, eksik journal'ın eski master'dan oluşturulmaması,
checkpoint ve eski sürümden yeniden yükseltmede yeni komut kayıtlarının korunması,
aktif durumun şifreli yedeği ve farklı boş UID ile temiz kurulum. Profil yokken
kalan hesap satırları devralınmaz; temiz USB + normal firmware hesap yenilemesi
ve aynı imzalı kurucunun tekrarı yolu [bakım kılavuzunda](backup-restore.md) yer alır.

## Yedek ve geri yükleme kaynağı (henüz dağıtılmadı)

İmzalı ayrı kullanıcı politikası seçiliyse `capture_current` READY durumda
bond ve veritabanını aktif runtime'dan alır; canonical ayar ve kimlik dosyaları
aynı yedek adlarıyla korunur. EXPORTED/PREPARED veya ilk hazırlık öncesinde
canonical veri seçilir. Her iki durumda SQLite ayrıştırması kısıtlı UID worker
içinde yapılır. READY verisi eksikse eski master'a geçiş yapılmaz.

`validate_agent_database_bytes`, geri yükleme baytlarını root'un oluşturduğu
tek geçici FD'ye yazar; dosyanın ve geçici yaprak dizinin sahibini aynı açık
FD'ler üzerinden ajan UID/GID'sine geçirir. Üst validation dizini root'a aittir.
Worker mevcut zaman/CPU/RAM/boyut/şema sınırlarını uygular. Root yalnız snapshot
baytlarını alır. Temizlik tek sabit dosyayı ve boş dizini kaldırır; ajan
tarafından yazılabilen ağaçta root olarak recursive gezinme yapılmaz.

Mevcut kurulum restore doğrulaması ve temiz kurulumun açık imzalı sürüm
doğrulaması bu worker'ı kullanır. Normal restore'un stop sonrası checkpoint'i
tamamlanmış EXPORTED kaydını ve canonical sidecar yokluğunu gerektirir;
root içinde SQLite açmaz. İmzalı yeni paketin bootstrap kurulumu hesapları
önceden hazırlar; restore dry-run hesap oluşturmaya başlamaz.

Tam şifreli arşiv -> servis stop -> restore -> ayrı UID başlatma ve başlatma
hatasında önceki güncel kayıtlara geri dönüş native fixture'da geçti. Temiz USB,
eski native hesapların reddi ve firmware tarzı yenilenmeden sonra farklı boş
UID'ye yedek dönüşü de test edildi. Bozulmuş/kayıp READY için yerinde zorla
devam edilmez; temiz kurulum + sağlam şifreli yedek kurtarma yoludur. Çalışan
sistemi veya üretim canonical master'ını bu testler değiştirmedi.

Bir birleşik tekrarlı yedek çalıştırmasında snapshot alt süreci 20 saniyelik
sınıra takıldı. Ayrı ve ölçümlü birleşik tekrarlar geçti; kök neden henüz
kanıtlanmadı. Sınırlar gevşetilmedi. Bu aralıklı hata sonuçlardan çıkarılmaz.

2026-11-01 ek izole tekrarda şifreli snapshot testi geçti (1 test,
32,326 saniye; üretim verisi kullanılmadı). Gözlenen dokuz worker çağrısının
en yavaşı 7,178 saniye sürdü. Bu çağrıda süreç çeşitli örneklerde
`__lock_page_killable` bekleme kanalında D durumundaydı; başka bir çağrıda
`wait_on_page_bit` gözlendi. Bu ölçüm sayfa/disk beklemesiyle uyumludur,
fakat önceki 20 saniyelik zaman aşımının nedenini veya depolama arızasını
kanıtlamaz. Test sentetik USB/Tunnel kullanır; canlı yedek kabulü değildir.

## USB ve hotplug durumu

Üretim USB düğümüne kalıcı izin henüz verilmedi. `usb_policy.target_for_usb()`
VID/PID + tam seri numarası + tek eşleşme + bus/device/topoloji sınırlarını
doğrular; yol olay parametresinden alınmaz. Seçicinin gerçek aygıt major/minor
numarasıyla eşleşmesi salt okunur test edilmiştir.

Keenetic'in `/opt/etc/ndm/usb.d` mekanizması cihazda mevcuttur. Start/stop
olayları yalnız tetikleyici olmalı; root helper her seferinde sabit sysfs alanını
yeniden tarayıp karakter aygıtı türü, rdev, inode ve aygıt kimliğini doğrulamalıdır.
İzin tüm USB ağacına uygulanmaz. İlk başlatma da aynı dar hazırlığı UID düşürmeden
önce yapmalıdır. İmzalı `service/hue-usb-event`, bootstrap kurulumu sırasında
`/opt/etc/ndm/usb.d/50-keenetic-hue` konumuna özel root script olarak yazılır.
Geri dönüşte seçili eski politika veya durmuş owner izin vermez. `huectl
usb-event` yol/aygıt parametresi kabul etmez. Kaynak ve yerel kesinti/kurulum
testleri hazırdır; imzalı hook cihazda kuruludur. Geçici RC6 testinde kullanıcı
adaptörü çıkarıp taktıktan sonra yeni USB düğümüne gerekli grup izni verildi,
BLE ve bulut durum sorgusu doğrulandı. Ancak kernel günlüğünde aynı sırada USB
hub ve Entware belleğinin de bağlantısını kaybettiği görülüyor. Bu nedenle tek
başına adaptörün kesildiği bir hotplug testi ve kalıcı RC6 kabulü kanıtlanmış
değildir. SSH ve test koruması süreçleri de kayboldu; yönetimli RC5 geri dönüşü
sonradan elle çalıştırıldı ve sağlık kontrolü geçti. Geçici test kaydındaki eski
armed işareti çalışan bir süreç anlamına gelmez. Kesintinin kesin nedeni ve
depolama kaybında kurtarma davranışı ayrıca incelenmelidir.

2026-11-01 sonraki salt okunur topoloji kontrolünde hub `1-2`, Entware
belleği `1-2.1` ve Belkin adaptörü `1-2.4` olarak görüldü; `/opt` ext4
`/dev/sda1` üzerindeydi. Ayrı hub portları olmaları, hub'ın veya güç
beslemesinin bağlantı kesintisinden etkilenmeyeceği anlamına gelmez. Bu
gözlem RC6 fiziksel kabul testi veya önceki kesintinin nedeni değildir.

Sonraki salt okunur kernel log kontrolünde USB hub'ın yeniden görünmesinden
önce `/dev/sda1` için ext4 dizin okuma hataları ve önceki superblock I/O
hatası mesajları gözlendi. Logda son ext4 bağlanmasından sonra aynı filtreyle
eşleşen yeni hata yoktu; `/opt` `rw,noatime,data=ordered` bağlıydı. Bu,
dosya sisteminin tutarlı olduğunu kanıtlamaz. `e2fsck` cihaz PATH'inde yoktu.
Bağlı `/opt` üzerinde onarım çalıştırılmadı. Korunan yedeğin USB dışında
doğrulanması ve dosya sistemi kontrolü için çevrimdışı yöntem belirlenmeden
yeni USB kesinti denemesi yapılmamalı. Önceki snapshot yavaşlığının bu
olaydan kaynaklandığı da kanıtlanmış değildir.

Kaynaklar:

- [Keenetic OPKG callback açıklaması](https://support.keenetic.com/peak/kn-2710/en/42407-opkg-component-description.html)
- [Linux no_new_privs](https://docs.kernel.org/userspace-api/no_new_privs.html)
- Bumble0.0.236 kurulu JsonKeyStore.save/load/update kaynakları cihazda inspect
  ile okunmuştur; saklanan anahtarlar incelenmemiş/yazdırılmamıştır.
