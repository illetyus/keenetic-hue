# RAM üzerinden USB bakım paketini hazırlama

`deploy/storage_ram_prepare.py`, gelişmiş USB dosya sistemi bakımında gereken
Python ve doğrulanmış kontrol araçlarını Keenetic'in RAM'ine hazırlayan bilgisayar
tarafı yardımcısıdır. Normal Hue kurulumu, lambanın kontrolü ve otomatik başlangıç
bu betiğe bağlı değildir. Betik imzalı router paketine dahil edilmez.

## Önce kendi bağlantı bilgilerini doldur

Depo kökünde örnek dosyanın yerel kopyasını oluştur. PowerShell için:

```powershell
Copy-Item deploy/storage-ram.example.json .storage-ram.local.json
```

`.storage-ram.local.json` dosyasını bir metin düzenleyiciyle açıp bütün alanları
kendi sistemine göre doldur:

| Alan | Yazılacak bilgi |
| --- | --- |
| `ssh_host` | Keenetic'in kendi ağındaki IP adresi veya ana bilgisayar adı. |
| `ssh_port` | Entware SSH sunucusunun portu; JSON tamsayısı olmalı. Keenetic OS CLI portuyla aynı olduğu varsayılmaz. |
| `ssh_user` | Entware'e girişte kullandığın SSH kullanıcı adı. |
| `ssh_key_path` | Bilgisayarındaki SSH özel anahtar dosyasının yolu; anahtarın içeriği veya parola değil. |

Windows dosya yollarını JSON içinde `C:/klasor/anahtar` biçiminde yazabilirsin.
Göreli anahtar yolları ayar dosyasının bulunduğu klasöre göre çözülür; `~` ev
klasörüne genişletilir. Betik anahtar dosyasını değiştirmez. Anahtarın işletim
sistemindeki özel erişim izinlerini koru.

Boş örnek çalıştırılabilir bir yapılandırma değildir. Ayar eksik/hatalıysa veya
anahtar dosyası yoksa betik, araç yükünü okumadan ve SSH bağlantısı kurmadan
`STORAGE_SETTINGS_...` hatasıyla durur. Hazır bir adres veya anahtar yolu seçmez.

SSH hedefinin sunucu anahtarını önce güvenilir yolla doğrulayıp kendi OpenSSH
`known_hosts` kaydına ekle. Betik `StrictHostKeyChecking=yes`, `IdentitiesOnly=yes`
ve `BatchMode=yes` kullanır; bilinmeyen anahtarı otomatik kabul etmez.

`.storage-ram.local.*` dosyaları Git tarafından yok sayılır. Kendi adresini ve
anahtar yolunu doldurduğun dosyayı veya bakım günlüklerini repoya ekleme. Başka
adla bir ayar/günlük dosyası kullanırsan onu ayrıca Git dışında tut.

## Kullanım

Bilgisayarda Python ve OpenSSH istemcisi, Keenetic'te mevcut anahtarlı Entware
SSH erişimi gerekir. `TOOL_PAYLOAD.json`, betiğin sabitlenmiş araç özetleriyle
eşleşen IPK oluşturucusunun araç yükü olmalıdır; rastgele bir dosya değildir.

Depo kökünde yalnız hazırlık için:

```sh
python deploy/storage_ram_prepare.py TOOL_PAYLOAD.json
```

Başka bir yerel ayar dosyası veya isteğe bağlı günlük için:

```sh
python deploy/storage_ram_prepare.py TOOL_PAYLOAD.json --settings LOCAL_SETTINGS.json --log-file .storage-ram.local.log
```

Varsayılan `--action prepare` yalnız RAM bakım paketini hazırlar; kontrolü
başlatmaz. `--action start` ayrı bir açık işlem olarak bakım akışını başlatır.
`--action status --operation-id OPERATION_ID`, ilgili işlem kimliğiyle durumu
sorgular. Kaynak, araç özeti ve izin doğrulamaları değişmez; biçimlendirme aracı
bakım yüküne alınmaz. Bu yardımcı betiği rutin lamba açma/kapatma amacıyla kullanma.
