# Mimari ve sınırlar

![Bileşenler ve veri akışı](assets/architecture.svg)

Google Home komutu, kullanıcıya ait geliştirme entegrasyonu üzerinden Worker'a
gelir. Worker OAuth bağını ve komut akışını yönetir; Tunnel'ın dışarı doğru
kurduğu bağlantıyla Keenetic'teki yerel uçla konuşur. Entware ajanı USB Bluetooth
adaptörüyle ampulü kontrol eder. HomeGraph bildirimi ayrı doğrulanır.

| Katman | Sorumluluk |
| --- | --- |
| Google / Worker | Hesap bağlama, sınırlı komut yetkisi, durum raporlama |
| Tunnel | Yerel uca ağ yolu; tek başına komut yetkilendirmesi değildir |
| Yönetim | İmzalı sürüm, kimlik/izin, atomik seçim, yedek/kurtarma |
| Ajan kimliği | BLE işlemleri ve ajan veritabanı |
| Fiziksel dünya | Ampul elektriği, adaptör, radyo bağlantısı ve dış müdahale |

PID tek başına sahiplik kanıtı değildir; boot kimliği, süreç başlangıcı,
argümanlar ve beklenen kimlik doğrulanır. Günlükler sınırlıdır; kapanış kanıtı
yoksa neden bilinmez. Kuyruğa kabul, fiziksel tamamlanma anlamına gelmez.
`verified` son okumaların istenen durumla eşleşmesidir. Belirsiz sonuç
`OUTCOME_UNKNOWN` olarak saklanır ve otomatik tekrar gönderilmez.

Sıcak SQLite journal kalırsa root özgün DB/journal çiftini opak özel dosyalar
olarak korur. Yalnız geçici kopya, ayrı ajan kimliği ve kaynak sınırlarıyla
kurtarılır. Şema/bütünlük doğrulanmadan kanonik durum değiştirilmez. Eski runtime
saklanmadan yeni DB ile eski journal birleştirilmez. Ajan başarısızsa birlikte
başlangıçta Tunnel atlanır.

RC12 mevcut kurulumda komut/reboot kabulünü geçti. Bu, yeni hesapları veya
her kesinti türünü doğrulamaz. İlk tarihsel supervisor kaybının tetikleyicisi
bilinmiyor; düzeltilen sorun kanıtlanan kurtarma kilitlenmesi ve tanı eksikliğidir.
