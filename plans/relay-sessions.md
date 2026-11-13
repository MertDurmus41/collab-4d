# Relay sunucu ve oturum kodu planı

Durum: **yalnızca tasarım, kod yazılmadı.** Tarih: 2026-11-10. Temel alınan sürüm: `main` @ `5e9e14a`.

Not (2026-11-11): Blender'daki Multi-User'ın yolu (ZeroTier/Tailscale ya da ücretsiz bir bulut
sunucusunda Cinema 4D'siz sunucu, ilk katılan sahnesini paylaşır) para gerektirmeden bugün çalışıyor;
bkz. [research/MultiUser_Baglanti.md](../research/MultiUser_Baglanti.md). Bu plan, bunun üstüne
"yalnızca kod yaz, hiçbir şey kurma" kolaylığını ekleyen ücretli/işletilen seçenek olarak duruyor.

Amaç: Avrupa, Asya ve Amerika'daki Collab 4D kullanıcıları IP adresi, port, modem port yönlendirmesi
veya VPN ile uğraşmadan aynı sahnede çalışabilsin. Kullanıcı yalnızca `K7P-42X` gibi kısa bir oturum
kodu paylaşsın.

## 1. Özet ve öneri

- İnternette her zaman açık küçük bir **relay** sunucusu çalışır. Host da misafirler de bu sunucuya
  **dışarı doğru** bağlanır (TCP 443, TLS). Kimsenin modeminde port açması gerekmez; CGNAT sorun olmaz.
- Host "Oturum başlat"a basınca relay bir **oturum kodu** verir. Misafir kodu ve şifreyi yazıp katılır.
- Önerilen mimari **tünel relay** (Seçenek B, aşağıda): sahnenin yetkili sunucusu bugün olduğu gibi
  host'un Cinema 4D'sinde kalır; relay yalnızca iki ucu eşleştirip baytları taşır.
- Bugünkü doğrudan Host address + Port yolu **silinmez**, panelde "Gelişmiş: doğrudan bağlantı (LAN)"
  altında kalır.
- Barındırma, alan adı ve bölge sayısı para gerektirir: **seçim sana ait**, 9. bölümdeki sorulara
  cevap gelmeden hiçbir şey satın alınmayacak / kurulmayacak.

## 2. Bugünkü durum (koddan okunan)

| Parça | Bugün ne yapıyor |
|---|---|
| `core/server.py` `SessionServer` | Süreç başına **tek oturum**. `AF_INET` (yalnız IPv4) dinler, `select` döngüsü, yetkili sahne modeli + kilit tablosu. Kimlik doğrulama öncesi 64 KB çerçeve sınırı, saniyede 1000 mesaj sınırı, IP başına 5 hatalı şifrede 60 sn yasak. |
| `core/client.py` `CollabClient` | `socket.create_connection((host, port))`, arka plan iş parçacığı, `select` ile okuma/yazma. |
| `core/net.py` `Connection` | Bloklamayan düz TCP soketi + çerçeve çözücü. TLS bilmiyor. |
| `core/protocol.py` | `>IB` başlık + JSON (64 KB üstü zlib). `CHALLENGE` → `HELLO(proof=HMAC-SHA256(şifre, nonce))` → `WELCOME`. Şifre ağda gitmez; **geri kalan her şey şifresiz**. |
| `core/session.py` `CollabSession` | `host()`: sunucuyu `0.0.0.0:port`'ta açar, kendi istemcisiyle `127.0.0.1`'e bağlanır (host sahneyi sunucusuna istemci olarak yükler). `join(address, port, ...)`. Host'a şifre zorunlu (en az 4 karakter). |
| `c4d_adapter/ui_dialog.py` | Your name, Host address, Port, Password; Host session / Join / Leave. Ad, adres, port hatırlanır; şifre hatırlanmaz. |
| `server/run_server.py` + Docker | Cinema 4D'siz aynı `SessionServer`; `--host/--port/--password/--name/--max-clients`. Tek oturum. |

`server.py`, `protocol.py` ve `model.py` Aşama 8'den (`121e3cf`) beri değişmedi: çekirdek protokol
kararlı, sürüm numarası hâlâ 1.

## 3. Mimari: iki seçenek

### Seçenek A - "Barındırılan oturum" (relay sahneyi tutar)

Relay, her kod için bir `SessionServer` çalıştırır. Host da sıradan bir istemci gibi bağlanır ve
sahneyi relay'e yükler.

### Seçenek B - "Tünel" (relay yalnızca taşır) - **önerilen**

Host'un Cinema 4D'si bugünkü gibi kendi `SessionServer`'ını çalıştırır ama internete port açmaz.
Bunun yerine relay'e dışarı doğru bir **kontrol bağlantısı** kurar. Bir misafir kodu yazınca relay
host'a haber verir, host o misafir için relay'e ikinci bir dış bağlantı açar, relay iki soketi
birbirine bağlar. O andan sonra aradan geçen baytlar bugünkü protokolün aynısıdır
(`CHALLENGE/HELLO/WELCOME/...`).

| Ölçüt | A - barındırılan | B - tünel |
|---|---|---|
| Relay'in RAM / CPU ihtiyacı | Yüksek: her sahne (dokular dahil) relay RAM'inde, her mesaj relay'de JSON olarak çözülür | Çok düşük: bayt kopyalar, sahne tutmaz |
| Relay başkalarının projesini saklar mı | Evet (RAM'de) - başkasının NDA'lı sahnesinin sorumluluğu sende | Hayır. (TLS relay'de bittiği için geçen trafiği *teknik olarak* görebilir, ama saklamaz) |
| Eklenti güncellenince relay'i de güncellemek gerekir mi | Evet, protokol/model değişirse; eski ve yeni eklentiler aynı relay'de sorun çıkarabilir | Hayır: relay sahne protokolünü bilmez |
| Host'un yükleme hızı yavaşsa | İyi: host sahneyi **bir kez** yükler, relay herkese dağıtır | Bugünkü gibi: host sahneyi her misafire kendi yükleme hızıyla gönderir |
| Host'un C4D'si çökerse | Oturum yaşayabilir, host geri dönüp sahneyi alabilir | Oturum biter (bugünkü davranış) |
| Eklenti tarafındaki iş | Az (host = ilk istemci) | Biraz daha fazla (host tarafına "relay bağlantısı" eklenir) |
| Bugün canlı test edilmiş yetkili sunucu yolu | Relay'e taşınır | Aynen kalır |
| Gelecekte uçtan uca şifreleme | Mümkün değil (relay sahneyi okumak zorunda) | Mümkün |

**Öneri: B.** Ücretsiz, tek kişinin baktığı bir toplum hizmeti için en ucuz, en az bakım isteyen ve
başkalarının verisini saklamayan seçenek bu. A'nın gerçek üstünlüğü büyük sahne + yavaş yükleme
hızı + çok misafir durumunda ve çökme sonrası devam etmede; ileride "kalıcı oda" özelliği olarak
B'nin üstüne eklenebilir (bkz. açık soru 1).

Planın geri kalanı B'yi ayrıntılandırır; A seçilirse değişecek yerler 8. bölümde belirtildi.

## 4. Çok oturumlu relay

### 4.1 Oturum kodu

- Biçim: **6 karakter**, ekranda `K7P-42X` olarak gösterilir. Girişte tire, boşluk ve küçük/büyük
  harf farkı yok sayılır.
- Alfabe 31 karakter: `23456789ABCDEFGHJKMNPQRSTUVWXYZ` (karışan `0/O`, `1/I/L` yok).
  31^6 ≈ 887 milyon kod.
- **İlk karakter bölgeyi söyler** (ör. `E…` Avrupa, `A…` Amerika, `S…` Asya; her bölgeye birkaç harf
  ayrılır). Böylece misafir doğru relay'e doğrudan gider, merkezi bir rehber sunucusu gerekmez ve
  sonradan bölge eklemek eski eklentileri bozmaz. Tek bölgeyle başlansa da bu kural ilk günden konur.
- Kod relay'de `secrets` ile rastgele üretilir, oturum bitince serbest kalır, 24 saat yeniden verilmez.
- Kod **gizli anahtar değildir**; asıl koruma şifredir (4.4) ve kod deneme sınırıdır (7.3).

### 4.2 Relay mesajları

Aynı çerçeve biçimi (`>IB` + JSON) kullanılır; yeni tipler `protocol.py`'ye eklenir ve yalnızca
bağlantının **ilk** mesajı olarak geçerlidir. Hepsinde `rv` (relay protokol sürümü) ve eklenti sürümü
bulunur; relay çok eski bir eklentiyi anlaşılır bir "güncelle" mesajıyla reddedebilir.

```
Host   -> Relay : relay_create {rv, plugin, max_guests}
Relay  -> Host  : relay_created {code, owner_token, limits}        (kontrol bağlantısı açık kalır)

Misafir-> Relay : relay_join {rv, plugin, code}
Relay  -> Host  : relay_guest {guest_id, ip_tag}                   (kontrol bağlantısından)
Host   -> Relay : relay_accept {code, owner_token, guest_id}       (YENİ dış bağlantı)
Relay  -> Misafir: relay_ok                                        (iki soket bağlandı)
        ... bundan sonrası: bugünkü protokol, relay yalnızca kopyalar ...

Relay  -> herkes: relay_error {code: no_such_session | full | rate_limited | upgrade_required | busy}
Kontrol bağlantısında: relay_ping / relay_pong (15 sn'de bir)
```

- `ip_tag`: misafirin IP'sinin relay'e özgü kısa özeti. Host'taki `SessionServer` "5 hatalı şifrede
  yasak" kuralını bununla uygular (yoksa bütün misafirler tek adresten geliyor görünür).
- Host kendi istemcisiyle bugünkü gibi `127.0.0.1` üzerinden bağlanır; relay'den geçmez.

### 4.3 Sahiplik ve oturumun bitişi

- Oturumun sahibi, `relay_create` yapan kontrol bağlantısıdır; `owner_token` yalnızca ona verilir ve
  `relay_accept` için gerekir (başkası host'un yerine misafir kabul edemez).
- Oturum şu durumlarda biter: host **Leave** der / oturum belgesini kapatır (bugünkü kurallar aynen),
  kontrol bağlantısı koparsa, azami yaş dolarsa (öneri: 12 saat, 10 dk önce uyarı), relay yeniden
  başlarsa.
- **Kısa kopma toleransı (Aşama 4, isteğe bağlı):** kontrol bağlantısı koparsa relay kodu 60 sn
  tutar; host `owner_token` ile `relay_resume` yaparsa kod değişmez. Bağlı misafir tünelleri ayrı
  soketler olduğundan zaten etkilenmez.

### 4.4 Şifre

- Bugünkü HMAC şifre doğrulaması **değişmeden** tünelin içinden geçer; şifreyi host'un C4D'si
  doğrular. Relay şifreyi hiç görmez, bilmez.
- Öneri: relay modunda da şifre zorunlu kalsın (en az 4 karakter). Kolaylık için panel "Daveti
  kopyala" ile `Kod: K7P-42X  Şifre: ...` metnini panoya koyar (açık soru 4).

### 4.5 Sınırlar (başlangıç değerleri, hepsi relay ayarı)

| Sınır | Değer |
|---|---|
| Oturum başına kullanıcı | 16 (bugünkü `max_clients`) |
| Aynı anda oturum (relay başına) | 200 |
| IP başına açık oturum / saatte yeni oturum | 3 / 10 |
| IP başına kod denemesi | dakikada 10; 20 yanlış kodda 10 dk yasak |
| İlk mesaj için süre ve boyut | 10 sn, 4 KB |
| Misafir bekleme (host `relay_accept` göndermezse) | 10 sn |
| Oturum başına veri | saatte 20 GB, üstünde yavaşlatma (dosya tüneli olarak kullanılmasın) |
| Oturum azami yaşı | 12 saat |
| Trafik olmayan tünel | 60 sn sonra kapanır (istemciler zaten 3 sn'de bir ping atıyor) |

## 5. TLS

### 5.1 Ne şifrelenir

Eklenti ↔ relay arasındaki **her bağlantı** TLS olur (host kontrol bağlantısı, host'un misafir
başına açtığı bağlantılar, misafir bağlantıları). Eklenti yalnızca TLS **istemcisi** olur; sertifika
üretmesi veya TLS sunucusu olması gerekmez. Relay sunucusu yalnızca modern Python'da (3.12+) çalışır;
Python 2.7 kısıtı relay kodu için geçerli değildir.

Dürüst sınır: TLS relay'de biter. Yoldaki kimse (Wi-Fi, servis sağlayıcı) trafiği okuyamaz, ama
relay'i işleten kişi teknik olarak görebilir. Tam uçtan uca şifreleme bu planın kapsamı dışında
(Python 2.7'nin standart kütüphanesinde uygun şifre yok); B mimarisi buna ileride izin verir.

### 5.2 Eski Python'ların `ssl` modülü: ne yapabiliyor

Bu makinede **ölçüldü** (2026-11-10):

| | R19 (Python 2.7.9) | 2023 (Python 3.9.1) |
|---|---|---|
| OpenSSL | 1.0.1j (2014) | 1.1.1i |
| En yüksek TLS | **1.2** (1.3 yok) | 1.3 |
| `SSLContext`, SNI, `check_hostname` | var | var |
| `PROTOCOL_TLS_CLIENT` | **yok** → `PROTOCOL_SSLv23` + `OP_NO_SSLv2/3/TLSv1/TLSv1_1` | var |
| `load_verify_locations(cadata=...)` | çalışıyor | çalışıyor |
| ECDHE + AES-GCM şifreleri | var | var |
| `SSLWantReadError` / `pending()` | var | var |
| `MemoryBIO` (asyncio tarzı TLS) | **yok** | var |

Deneme: Python 3.14 / OpenSSL 3.0 ile TLS 1.2 alt sınırlı bir sunucu ve kendi imzaladığımız bir CA
kuruldu. R19'un Python'u **TLS 1.2 / ECDHE-ECDSA-AES256-GCM-SHA384** ile, 2023'ünkü **TLS 1.3** ile
bağlandı; ikisi de bloklamayan soket + `select` ile 20 MB veriyi eksiksiz geri aldı; ikisi de yanlış
alan adını ve tanınmayan CA'yı reddetti. Yani tasarımın en riskli varsayımı Windows'ta doğrulandı.

**Doğrulanmadı (test edilmeli):**
- R17, R18 (R19 ile aynı Python 2.7.9 olduğu *sanılıyor*), R20-S22 (2.7.14 sanılıyor), R23 (3.7),
  2024-2026 (3.11). Python 2.7.9'dan eski bir sürüm çıkarsa `SSLContext` yoktur; o sürümde relay
  kullanılamaz.
- **macOS'un tamamı.** Eski macOS Python'ları sistemin OpenSSL 0.9.8'ine bağlı olabilir; o zaman
  yalnızca TLS 1.0 konuşur ve relay bunu kabul etmemelidir. Mac'te R17-R22 kullanıcıları için relay
  çalışmayabilir (açık soru 6).
- `ssl` modülü olmayan bir Cinema 4D derlemesi olup olmadığı.

Eklenti açılışta `ssl` yeteneğini yoklar. Yetersizse panel relay bölümünü kapatır ve açık bir mesaj
gösterir: "Bu Cinema 4D sürümü güvenli internet bağlantısını desteklemiyor; doğrudan bağlantıyı
kullan." **Şifresiz relay bağlantısına sessizce düşülmez.**

### 5.3 Sertifika güveni: sabitlenmiş kendi CA'mız

- Küçük bir **Collab 4D kök sertifikası** (ECDSA P-256, 20 yıl) üretilir; özel anahtarı çevrimdışı
  saklanır. Bu kökün PEM metni eklentinin içine bir Python sabiti olarak gömülür (`cadata` ile
  yüklenir; dosya yolu kullanılmaz, böylece Türkçe karakterli kullanıcı klasörleri Python 2'de sorun
  çıkarmaz).
- Relay'in kullandığı sunucu sertifikası bu kökle imzalanır (1 yıl, SHA-256, SAN = relay alan adları).
  Yenilemek eklenti güncellemesi gerektirmez.
- Eklenti **yalnızca** bu köke güvenir, alan adını da denetler. İşletim sisteminin sertifika deposuna,
  Let's Encrypt'in zincir değişikliklerine veya eski Windows'ta eksik kök sertifikalara bağımlılık
  kalmaz. Her Python sürümünde aynı davranır.
- Yedek: eklentiye **iki** kök gömülür (biri kasada yedek); biri kaybolur/sızarsa eklenti
  güncellemesi beklemeden diğerine geçilir.
- Seçenek olarak Let's Encrypt (ISRG kökleri gömülü) da mümkün; otomatik yenileme ister ve zincir
  değişikliklerine açıktır, bu yüzden önerilmiyor (açık soru 5).
- Relay ayarı: en düşük TLS 1.2, yalnız ECDHE + AEAD şifreleri, sıkıştırma kapalı.

### 5.4 Koddaki zorluklar

- `ssl.wrap_socket` ve `match_hostname` Python 3.12'de kaldırıldı: her sürümde yalnızca
  `SSLContext.wrap_socket(server_hostname=...)` + `check_hostname=True` kullanılır.
- El sıkışma, bağlanan iş parçacığında **bloklayan** modda ve zaman aşımıyla yapılır; sonra soket
  bloklamayan moda alınır (eski Python'da bloklamayan el sıkışma kırılgan).
- `net.Connection` TLS'i bilmeli: `SSLWantReadError/SSLWantWriteError` "şimdilik veri yok" sayılır;
  `recv` sonrasında `pending()` > 0 iken okumaya devam edilir (`select` çözülmüş ama okunmamış veriyi
  görmez); yarım kalan `send` aynı baytlarla yeniden denenir.
- Kaynak dosyalar ASCII kalır (R19, kodlama satırı olmayan ASCII dışı karakterde eklentiyi yüklemiyor).

## 6. Panel değişiklikleri (`ui_dialog.py`)

Varsayılan görünüm (bağlı değilken):

```
Your name      [ Mert            ]
Password       [ ........        ]

[ Start session ]

Session code   [ K7P-42X ]   [ Join ]

> Advanced: direct connection (LAN)        <- kapalı gelir
    Host address [            ]  Port [47800]
    [ Host on this network ]  [ Join by address ]
```

Oturumdayken:

```
Hosting - code K7P-42X - 3 user(s) - 84 ms        [ Copy invite ]   [ Leave ]
```

- "Start session": relay'e bağlanır, kodu alır, durum satırında büyük gösterir. "Copy invite" kodu
  (ve seçilirse şifreyi) panoya koyar.
- "Join": kod alanı doluysa relay ile katılır. Kod yapıştırılırken boşluk/tire/küçük harf düzeltilir.
- Gelişmiş bölüm bugünkü üç alanı ve davranışı aynen taşır (`session.host()` / `session.join()`).
  R17'de açılır-kapanır grup yoksa bir onay kutusu ("Show direct connection") ile gösterilip gizlenir;
  düzen yine kodla kurulur, `.res` dosyası yok.
- Hata mesajları kullanıcı diliyle: "Bu kodla açık bir oturum yok", "Oturum dolu", "Relay'e
  ulaşılamıyor - internetini kontrol et ya da doğrudan bağlantıyı dene", "Eklentini güncelle".
- Hatırlananlar: ad, son kullanılan mod, adres, port. **Kod ve şifre hatırlanmaz.**
- Relay adresi eklentide sabit bir listedir (bölge → alan adı). Kendi relay'ini kuran stüdyolar için
  ortam değişkeni `C4D_COLLAB_RELAY=host:port` ile değiştirilebilir (panele alan eklenmez).

## 7. Barındırma (karar senin - henüz hiçbir şey seçilmedi)

### 7.1 Nerede

Relay tek bir küçük Docker konteyneridir (Python 3.12, bağımlılık yok). B mimarisinde 1 vCPU / 1 GB
RAM yüzlerce oturuma yeter; asıl tüketilen şey **bant genişliği**dir (her sahne relay'den bir kez
girer, bir kez çıkar). Bu yüzden GB başına ücret alan sağlayıcılar yerine trafik dahil sabit fiyatlı
bir VPS mantıklı.

Fiyatlar hafızamdan, yaklaşık; satın almadan önce güncel fiyat kontrol edilmeli:

| Seçenek | Aylık (yaklaşık) | Artı | Eksi |
|---|---|---|---|
| Hetzner Cloud (küçük VPS) | ~5 € / bölge | AB'de çok yüksek dahil trafik; Almanya, Finlandiya, ABD doğu/batı, Singapur | ABD/Singapur'da dahil trafik daha az |
| Vultr / DigitalOcean / Linode | ~5-6 $ / bölge | Tokyo, Singapur, Mumbai, Sidney dahil çok bölge | Dahil trafik 0,5-2 TB |
| Oracle Cloud "Always Free" | 0 | Ücretsiz, 10 TB çıkış | Hesap kapatma / boşta geri alma riskleri; güvenilirliği garanti değil |
| Fly.io | ~3-10 $ + trafik | Çok bölge kurmak kolay | GB başına ücret; büyük sahnelerde fatura belirsiz |
| Alan adı | ~10-15 $ / yıl | Sunucuyu eklenti güncellemeden taşıyabilmek için gerekli | - |

Kaba toplam: **tek bölge ≈ 5-6 €/ay**, **üç bölge ≈ 15-20 €/ay**, artı alan adı.

### 7.2 Bölge ve gecikme

B mimarisinde yol her zaman `misafir → relay → host`'tur. Bu yüzden en iyi relay **host'a yakın
olandır**; o zaman gecikme, doğrudan bağlantıya çok yakın olur. Kabaca gidiş-dönüş süreleri:

| Yol | Yaklaşık |
|---|---|
| İstanbul ↔ Frankfurt | 40-50 ms |
| ABD doğu ↔ Frankfurt | 85-100 ms |
| ABD batı ↔ Frankfurt | 150 ms |
| Singapur / Tokyo ↔ Frankfurt | 160-250 ms |
| Tokyo ↔ ABD batı | 100-120 ms |

- **Tek relay Frankfurt'ta:** host Avrupa/Türkiye'deyse herkes için iyi (ABD'li misafir ~130 ms,
  Asyalı ~250-300 ms; düzenleme için yeterli, sürükleme biraz gecikmeli hissedilir). Host ABD'de,
  misafir Asya'da ise trafik Avrupa'dan dolanır (~400 ms) - kötü durum bu.
- **Üç relay (Avrupa, ABD, Asya):** host "Start session"a basınca eklenti üç relay'e kısa bir ölçüm
  yapar, en yakını seçer; kodun ilk karakteri bölgeyi taşır. Dolanma sorunu kalkar.
- Öneri: **tek bölgeyle başla** (ilk kullanıcılar neredeyse orada), kod biçimi bölgeyi ilk günden
  taşıdığı için sonra bölge eklemek yalnızca sunucu kurmak + eklentideki listeye bir satır olur.

### 7.3 Kötüye kullanım koruması

- 4.5'teki sınırlar (IP başına oturum, kod denemesi, veri kotası, zaman aşımları).
- Relay **hiçbir zaman dışarı bağlantı açmaz**; yalnızca kendisine gelen iki bağlantıyı eşleştirir,
  yani açık vekil sunucu olarak kullanılamaz.
- Kimlik doğrulama öncesi küçük çerçeve sınırı, el sıkışma zaman aşımı, IP başına eşzamanlı bağlantı
  sınırı (yavaş bağlantı saldırılarına karşı).
- Sunucu: yalnız 443 ve anahtarla SSH açık, konteyner root olmayan kullanıcıyla, otomatik güvenlik
  güncellemeleri, çökünce otomatik yeniden başlama.
- Kayıt: içerik **asla** yazılmaz; yalnızca oturum sayısı, bayt sayıları ve kısaltılmış IP özeti,
  en çok 7 gün. README'ye kısa bir gizlilik notu.
- Acil durum: bir oturumu ya da IP'yi kapatan yönetim komutu ve "eski sürümleri reddet" ayarı.
- İzleme: dışarıdan ücretsiz bir "port açık mı" denetimi + relay'in günlük özet satırı.

## 8. Aşamalar

Her aşama kendi başına birleştirilebilir ve mevcut doğrudan bağlantıyı bozmaz.

### Aşama 0 - Doğrulama (kod yok ya da tek kullanımlık betik)
- R17/R18/R20-S22/R23/2024+ ve macOS'ta `ssl` yoklaması (bu planın 5.2'sindeki tablo). Elimizde
  olmayan sürümler için kullanıcılardan çıktı istenir.
- Dosyalar: `tests/c4d_manual/probe_ssl.py` (yeni), `tests/c4d_manual/CHECKLIST.md`.
- Bitti sayılır: desteklenen her sürüm için "relay çalışır / çalışmaz" tablosu README'de.

### Aşama 1 - TLS bilen bağlantı katmanı
- `core/tls.py` (yeni): yetenek yoklaması, sabitlenmiş CA ile istemci `SSLContext`'i, 2.7.9 ↔ 3.11+
  farkları tek yerde.
- `core/net.py`: `Connection` TLS soketini taşıyabilir (5.4).
- `core/client.py`: isteğe bağlı `tls=`/`relay=` parametresi; el sıkışma bağlanan iş parçacığında.
- Testler: `tests/core/test_tls.py` (yerel TLS sunucusuna karşı, Python 2.7 ve 3.x'te),
  `tests/compat/python_audit.py` kuralları.
- Bitti sayılır: mevcut bütün testler değişmeden geçer; TLS üzerinden yankı testi R19 Python'unda ve
  3.x'te geçer.

### Aşama 2 - Relay sunucusu
- `server/relay.py` (yeni, yalnız Python 3.12+, `asyncio`): oturum tablosu, kod üretimi, eşleştirme,
  bayt kopyalama (geri basınçla), sınırlar, kayıt.
- `server/Dockerfile.relay`, `server/docker-compose.relay.yml`, `server/README.md` (kurulum, CA ve
  sertifika üretme adımları, yenileme).
- `core/protocol.py`: `relay_*` mesaj sabitleri ve kod yardımcıları (`normalize_code`, `format_code`,
  bölge karakteri).
- Testler: `tests/relay/test_relay.py` (oluştur/katıl/bitir, yanlış kod, sınırlar, kopan host, büyük
  veri).
- Bitti sayılır: relay yerelde çalışır; betikle açılan iki düz soket relay üzerinden konuşur.

### Aşama 3 - Eklenti: relay ile host ve katılma
- `core/relay_link.py` (yeni): host'un kontrol bağlantısı, `relay_guest` gelince dış bağlantı açıp
  soketi sunucuya verme.
- `core/server.py`: `adopt(conn, address_tag)` (dinleyiciden gelmeyen bağlantıyı kabul etme),
  yalnız `127.0.0.1` dinleme seçeneği, yasak listesi `ip_tag` ile.
- `core/session.py`: `host_relay(name, password)`, `join_relay(code, name, password)`, `code`
  özelliği; `leave()` relay bağlantısını da kapatır.
- Testler: `tests/core/test_session.py` + `tests/core/test_security.py` eklemeleri (relay üzerinden
  tam oturum; yanlış şifre yasağı relay arkasında doğru IP'yi yasaklar).
- Bitti sayılır: Cinema 4D'siz testte iki `CollabSession` yerel relay üzerinden sahne eşitler.

### Aşama 4 - Panel
- `c4d_adapter/ui_dialog.py`: 6. bölümdeki düzen, ayarların kaydı, hata mesajları, "Copy invite".
- `tests/adapter/fake_c4d.py` + panel testleri, `tests/c4d_manual/CHECKLIST.md` yeni bölüm.
- İsteğe bağlı: 60 sn kopma toleransı (`relay_resume`).
- Bitti sayılır: R19 ve 2023'te elle test: kodla oturum, doğrudan bağlantı hâlâ çalışıyor.

### Aşama 5 - Yayın (senin onayınla, para gerektirir)
- Sağlayıcı + bölge + alan adı seçimi, CA üretimi, relay kurulumu, izleme.
- `README.md`, `ARCHITECTURE.md` (ağ bölümü), web sitesindeki panel rehberi.
- Bitti sayılır: iki farklı ülkedeki iki makine, modemde hiçbir ayar yapmadan kodla aynı sahnede.

### Aşama 6 - Sonrası (ayrı kararlar)
- İkinci ve üçüncü bölge; en yakın relay'i otomatik seçme.
- Seçenek A tarzı "kalıcı oda" (host çökse de yaşayan oturum).
- Uçtan uca şifreleme; IPv6 dinleme (doğrudan mod için).

**Seçenek A seçilirse:** Aşama 2'de `relay.py` bayt kopyalamak yerine kod başına bir `SessionServer`
çalıştırır (oturum başına iş parçacığı, bellek sınırı), Aşama 3'te `relay_link.py` ve `adopt()`
gerekmez (host yalnızca istemcidir), relay eklentiyle birlikte sürümlenir ve 7.1'deki sunucu en az
4 GB RAM ister.

## 9. Geçiş

- Doğrudan mod **aynen kalır** ve varsayılan port, şifre kuralı, LAN davranışı değişmez. Eski
  eklenti sürümleri LAN'da birbirleriyle çalışmaya devam eder.
- Sahne protokolü (`PROTOCOL_VERSION = 1`) değişmez; yalnızca relay'in ilk mesajları eklenir. Relay
  modundaki bir oturuma eski eklenti katılamaz (kod alanı yok) - beklenen durum, README'de yazılır.
- Kayıtlı ayarlar: eski `address`/`port` değerleri gelişmiş bölüme taşınır; kaydı olan kullanıcıda
  ilk açılışta yine de kod görünümü varsayılan olur.
- `server/run_server.py` ve bugünkü Docker imajı (tek oturumlu, kendi sunucusunu kuran stüdyolar
  için) korunur. İsteğe bağlı küçük ek: `--tls-cert/--tls-key`.
- Windows güvenlik duvarı sorusu relay modunda **çıkmaz** (dışarıya bağlantı); bu, panel rehberinde
  belirtilir.
- Relay kapalıysa ya da erişilemiyorsa panel bunu söyler ve doğrudan bağlantıyı / Tailscale'i
  önerir; eklentinin geri kalanı etkilenmez.

## 10. Test adımları

1. **Birim (C4D'siz), Python 3 ve R19'un Python 2.7'si:** `tests/core`, `tests/relay`, mevcut 581
   adaptör testi ve `tests/compat/import_check.py`.
2. **TLS matrisi:** `probe_ssl.py` çıktısı her erişilebilir sürümde; relay'e gerçek el sıkışma
   (TLS sürümü, yanlış alan adı reddi, tanınmayan CA reddi).
3. **Yerel relay ile gerçek Cinema 4D:** `tests/c4d_real` senaryosu relay üzerinden: 2023↔2023,
   2023 host → R19 misafir, R19 host → 2023 misafir (bugünkü 51/51 sonuçlarının aynısı beklenir).
4. **Kötü ağ:** relay önüne yapay gecikme (150 ms, 300 ms) ve %1 paket kaybı; sürükleme, snapshot,
   büyük dokulu sahne. Host'un interneti 30 sn kesilir → beklenen mesajlar.
5. **Kötüye kullanım:** yanlış kod denemeleri yasaklanıyor mu, IP başına oturum sınırı, veri kotası,
   bağlanıp susan istemci, 64 KB üstü ilk mesaj.
6. **Yük:** 100 sahte oturum × 4 istemci, relay'in RAM/CPU'su; bir oturumda 200 MB'lık sahne.
7. **Gerçek dünya:** farklı ülkelerde iki-üç kişi, modem ayarı yok, biri mobil internet (CGNAT)
   üzerinden; gecikme panelde okunur ve 7.2 tablosuyla karşılaştırılır.
8. **Elle kontrol listesi:** `CHECKLIST.md`'ye "Relay" bölümü (kodla başlat, kodla katıl, yanlış kod,
   yanlış şifre, host Leave, host belgeyi kapatır, relay kapalıyken hata, doğrudan mod regresyonu).

## 11. Riskler

| Risk | Etki | Önlem |
|---|---|---|
| Bazı C4D derlemelerinde `ssl` yok / TLS 1.2 yok (özellikle macOS R17-R22) | O kullanıcılar relay kullanamaz | Aşama 0'da ölçüm; açık mesaj + doğrudan mod; şifresiz düşüş yok |
| Kök sertifika anahtarı kaybolur ya da sızar | Herkesin eklentiyi güncellemesi gerekir | İki kök gömülü, anahtarlar çevrimdışı ve yedekli |
| Relay tek hata noktası | Kapanınca kodla oturum kurulamaz | Otomatik yeniden başlama, izleme, doğrudan mod her zaman var; sonra ikinci bölge |
| Sürekli maliyet ve bakım (ücretsiz eklenti) | Aylık gider, kötüye kullanımda artabilir | Sabit fiyatlı VPS, kotalar, kullanım istatistiği; gerekirse "kendi relay'ini kur" belgesi |
| Relay işleteni trafiği teknik olarak görebilir | Stüdyolar için güven sorunu | Dürüstçe belgelenir; kendi relay'ini kurma yolu; ileride uçtan uca şifreleme |
| B'de host'un yükleme hızı darboğaz (büyük sahne, çok misafir) | Katılma yavaş | Bugünkü davranışla aynı; gerekirse sonra "kalıcı oda" |
| Bloklamayan TLS soketlerinde ince hatalar (`pending`, yarım `send`) | Takılan ya da kopan bağlantı | Aşama 1'de 2.7 ve 3.x'te büyük veri testleri; el sıkışma bloklayan modda |
| Kod tahmini / tarama | Yabancı biri oturuma ulaşmaya çalışır | Deneme sınırı + zorunlu şifre (host doğrular) |
| Kurumsal güvenlik duvarları | 443 dışı portlar kapalı | Relay 443'te çalışır |
| Fiyat ve gecikme rakamları yaklaşık | Yanlış beklenti | Satın almadan önce güncel fiyat; Aşama 5'te gerçek ölçüm |

## 12. Sana açık sorular

1. **Mimari:** önerilen B (tünel) ile mi gidelim, yoksa A (relay sahneyi tutar) mı? B: ucuz, bakım
   az, veri saklamaz. A: yavaş internetli host için daha hızlı katılma, host çökse de oturum yaşar.
2. **Barındırma (para):** hangi sağlayıcı ve aylık bütçe? Öneri: trafik dahil sabit fiyatlı küçük bir
   VPS (~5 €/ay). Ücretsiz Oracle seçeneği de var ama güvenilirliği garanti değil.
3. **Bölge:** tek bölgeyle mi başlayalım (hangisi - kullanıcıların çoğu nerede?), yoksa baştan üç
   bölge mi (~15-20 €/ay)?
4. **Şifre:** relay modunda şifre zorunlu kalsın mı (öneri: evet), "Copy invite" şifreyi de
   kopyalasın mı?
5. **Alan adı ve sertifika (para):** bir alan adı alınacak mı (~10-15 $/yıl; elinde varsa bir alt
   alan adı yeter)? Sertifika için önerilen "kendi sabitlenmiş CA'mız" uygun mu?
6. **macOS ve eski sürümler:** Mac'te R17-R22 kullanan kullanıcıların relay'i kullanabilmesi şart mı,
   yoksa "o sürümlerde yalnızca doğrudan bağlantı" kabul edilebilir mi? Test edebileceğimiz bir Mac
   var mı?
7. **Sınırlar:** oturum başına 16 kişi ve 12 saat yeterli mi?
8. **Relay'i kim işletecek:** yalnızca sen mi; kayıt tutma süresi (öneri 7 gün, içerik yok) ve
   README'deki gizlilik notu uygun mu?
9. **Panel dili:** yeni düğme ve mesajlar bugünkü gibi İngilizce mi kalsın?
