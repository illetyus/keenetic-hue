# Bulut bağlantısı — günlük işletim

Bu sayfa eski G4 tek ajan düzeninin teknik notlarını güncel birleşik Hue/Entware işletim yordamıyla değiştirir. Çalışan kurulumda ikinci ajan başlatma, `/opt/var/lib/hue-agent` altındaki eski token dosyalarını düzenleme veya eski G4 HTTP uçlarını çağırma.

## Güvenli ilk kontrol

```sh
huectl status
huectl health --live --json
```

`status` sahip süreçleri; canlı `health` ise yerel API, BLE ve Tunnel durumunu denetler. Sağlıklı RC12 kontrolü `healthy: true` ve çıkış kodu `0` verir. Bu sorgular bulutta HomeGraph raporunun kabul edildiğini ölçmez; `reporting: unknown` veya `NOT_CHECKED` tek başına hata değildir.

## Hangi bileşen yeniden başlatılır

- Ajan/BLE sorunu: önce USB, Bluetooth adaptörü ve ampul elektriğini kontrol et; sonra gerekirse `huectl restart agent` ve `huectl health --live --json`.
- Yalnız Tunnel sorunu: WAN, DNS ve modem saatini kontrol et; gerekirse `huectl restart tunnel` ve canlı sağlık sorgusu.
- Yerel canlı sağlık iyi ama Google Home çevrimdışıysa bağlantıyı kaldırıp yeniden bağlama ilk adım değildir. Önce Worker/Tunnel/Google tarafını [bulut yeniden kurma kılavuzundan](cloud-rebuild.md) ayırarak incele.

Yeni bir Google hesabı bağlanacaksa mevcut bağlantıyı bozmadan terminalde `huectl setup-code` çalıştır. Kod 600 saniyede sona erer ve yalnız Google Home'da bir kez kullanılır; dosyaya veya günlüğe yönlendirme. Ayrıntı: [Google Home ve kurulum kodu](google-home.md).

Worker'ın genel `/health` yanıtı yalnız Worker'ın erişilebilir olduğunu gösterir; modemde BLE, API veya Google Home kontrolünü kanıtlamaz. Worker secret değerleri daha sonra okunamaz; kaynak/hesap yenileme durumunu ayrı doğrula.
