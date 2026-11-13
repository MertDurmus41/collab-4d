# C4D Collab — Elle Test Kontrol Listesi

Her sürüm (R19, 2023, 2024...) için uygulanır. Sonuçları en alttaki tabloya yaz.

## Kurulum
Geliştirme sırasında eklenti klasörü C4D'nin kullanıcı plugins klasörüne **bağlantı (junction)** olarak eklenir. Böylece kod değişince yeniden kopyalamak gerekmez, sadece C4D yeniden başlatılır.
- R19: `%APPDATA%\MAXON\Cinema 4D R19_BFE04C39\plugins\c4d_collab`
- 2023: `%APPDATA%\MAXON\Program Files_FC1C1B9E\plugins\c4d_collab`

## Aşama 2 — Bağlantı ve panel

| # | Adım | Beklenen sonuç |
|---|---|---|
| 2.1 | C4D'yi başlat, Python konsolunu aç (R19: Script → Console, 2023: Extensions → Console → Python) | `C4D Collab 0.1.0 loaded` yazar, kırmızı hata yok |
| 2.2 | Paneli aç (R19: **Plugins → C4D Collab**, 2023: **Extensions → C4D Collab**) | Ad, adres, port, şifre alanları ve Host / Join / Leave düğmeleri görünür. Şifre alanı yıldızlı (R19'da bu özellik yoksa açık görünebilir, not et) |
| 2.3 | 2023'te: ad yaz, port 47800, şifre `abc` → **Host session** | "en az 4 karakter" uyarısı, oturum açılmaz |
| 2.4 | 2023'te: şifre `test1234` → **Host session** | Windows Güvenlik Duvarı sorabilir: **sadece Özel ağlar** işaretli izin ver. Durum "Hosting - 1 user", altında "Others join with port 47800 at: <IP> (same network)" (Tailscale açıksa önce "Tailscale 100.x.y.z (any home)"); host değilken bu satırda Tailscale ipucu. Şifre alanı boşalır |
| 2.5 | R19'da: adres `127.0.0.1`, port 47800, şifre `yanlis` → **Join** | Log'da "wrong password", durum "Not connected" |
| 2.6 | R19'da: doğru şifre → **Join** | İki panelde de "2 user(s)" ve kullanıcı listesinde iki satır: biri `Python 3.9.x`, diğeri `Python 2.7.9` |
| 2.7 | 2023'te host olarak durumu izle | Gecikme (ms) görünür |
| 2.8 | R19'da **Leave** | 2023'te kullanıcı sayısı 1'e düşer |
| 2.9 | R19 tekrar Join, sonra 2023'te **Leave** (host kapatır) | R19 log'unda "Disconnected", durum "Not connected" |
| 2.10 | 2023'te tekrar Host, sonra C4D'yi kapat | C4D takılmadan kapanır. R19'da bağlantı kopar |
| 2.11 | Panelde ad/adres/port alanları | C4D yeniden açılınca hatırlanır, **şifre hatırlanmaz** |
| 2.12 | Log dosyası: Edit → Preferences → sol alttaki "Open Preferences Folder" | Klasörde `c4d_collab.log` var, içinde şifre geçmiyor |

## Aşama 3 — Sahne senkronizasyonu

**Hazırlık:** İki C4D'yi de yeniden başlat. Host (2023), içinde birkaç nesne olan bir sahne açsın: bir **Cube**, bir **Null** ve Cube'u Null'ın altına koy, Cube'a bir **Phong** tag'i ekle, bir de **Sphere**'i "Make Editable" ile poligona çevir. İstersen bir **Python tag** de ekle (gitmemesi gerekiyor).

| # | Adım | Beklenen sonuç |
|---|---|---|
| 3.1 | 2023'te Host, R19'da Join | R19'da **yeni bir belge sekmesi** açılır ("C4D Collab - ..."), sahne aynısıdır. Python tag **gelmez**. R19'da önceden açık olan belge değişmez |
| 3.2 | 2023'te Cube'un boyutunu (Size X) değiştir | R19'da da değişir |
| 3.3 | 2023'te Cube'u viewport'ta **fareyle sürükle** | R19'da hareket eder. Not et: sürükleme **sırasında** mı, yoksa **bıraktıktan sonra** mı güncelleniyor? (Collab paneli açık ve kapalıyken ayrı ayrı dene) |
| 3.4 | R19'da Null'ı yeniden adlandır (Türkçe harflerle, ör. "Kök Şekil") | 2023'te ad aynen görünür |
| 3.5 | R19'da yeni bir Cone oluştur | 2023'te de çıkar, aynı yerde |
| 3.6 | 2023'te Cube'u Null'ın altından çıkarıp en üste sürükle (Object Manager) | R19'da hiyerarşi aynı olur |
| 3.7 | R19'da nesnelerin sırasını değiştir | 2023'te sıra aynı olur |
| 3.8 | 2023'te bir nesneyi sil, sonra **Ctrl+Z** | R19'da önce silinir, sonra geri gelir |
| 3.9 | 2023'te Cube'u kopyala-yapıştır (Ctrl+C / Ctrl+V) | R19'da ikinci bir Cube çıkar (iki tane olmalı, biri diğerinin yerine geçmemeli) |
| 3.10 | 2023'te poligon nesnesinin bir noktasını taşı (Point modu) | Fareyi bırakınca R19'da da değişir |
| 3.11 | R19'da Cube'a yeni bir tag ekle (ör. Compositing), sonra sil | 2023'te önce çıkar, sonra kaybolur |
| 3.12 | R19'da oturumdan önce de açık olan başka bir sekmeye geç, orada bir şey yap, 10 sn dolmadan oturum belgesine dön | Panel log'u: "Sync paused ... within 10 s". Oturum belgesine dönünce: "Sync resumed". Diğer belgedeki değişiklikler **gitmez** |
| 3.12b | Aynı şekilde başka sekmede 10 sn'den uzun kal | "You left the session: you stayed in another project"; diğerlerinin kullanıcı listesinden düşersin (host isen oturum biter) |
| 3.12c | **Misafir iken** File > Open ile başka bir proje aç (ya da File > New) | Hemen: "You left the session: you opened another project"; panel "Connected" demez; host'un listesinden düşersin; yeniden Join edebilirsin |
| 3.12d | **Host iken** File > Open ile başka bir proje aç | "The session ended: you opened another project"; port kapanır; misafirler de oturumdan düşer |
| 3.12e | Oturum belgesini kapat | Oturumdan ayrılırsın (host isen oturum biter) |
| 3.13 | Üçüncü bir kullanıcı gibi: R19'da Leave, sonra tekrar Join | Yeni sekmede güncel sahne gelir |
| 3.14 | 10–20 saniye hiçbir şeye dokunmadan bekle | Log'da durmadan akan değişiklik yok, C4D yavaşlamıyor (yankı döngüsü yok) |

**Bilinen sınırlar (Aşama 3):** Kilit yok (Aşama 4), bu yüzden **aynı nesneyi aynı anda iki kişi düzenlemesin**. Animasyon/keyframe yok (Aşama 5). Materyaller gitmez (v1 sonrası). Büyük poligon modellerinde her değişiklikte tüm geometri gider (Aşama 6'da iyileşecek).

**Otomatik karşılığı:** 3.1, 3.2, 3.4–3.7, 3.10, 3.11 ve 3.14, gerçek C4D API'si üzerinde `tests/c4d_real` tarafından otomatik test edilir (bkz. ARCHITECTURE.md §8). Elle yapılması gerekenler: **3.3 (fareyle sürükleme)**, 3.8 (Ctrl+Z), 3.9 (kopyala-yapıştır), 3.12 (belge değiştirme, sahte C4D testinde var), 3.13.

## Aşama 4 — Kilitler

Otomatik testler (`tests/adapter/test_locks.py`, `tests/c4d_real`) kilit almayı, bırakmayı, seçimin geri alınmasını, izinsiz düzenleme/silme/taşımanın geri alınmasını ve yarış durumunu kapsar. Elle sadece fare ve menü davranışı denenir:

| # | Adım | Beklenen sonuç |
|---|---|---|
| 4.1 | 2023'te Cube'u seç | R19 panelinde "Being edited by others: Cube - <ad>" görünür |
| 4.2 | R19'da Cube'a tıkla | Seçilmez (ya da hemen bırakılır), durum çubuğunda "is being edited by ..." yazar |
| 4.3 | R19'da Attribute Manager açıkken Cube'un boyutunu değiştirmeye çalış | Değer geri döner, 2023'te değişmez |
| 4.4 | R19'da Object Manager'da Cube'u sürükleyip başka yere bırak | Yerine geri döner |
| 4.5 | 2023'te Cube'un seçimini kaldır | R19 listesinden kalkar, artık R19 düzenleyebilir |
| 4.6 | İkisi de aynı anda aynı nesneye tıklasın | Sadece biri seçili kalır, sahneler aynı kalır |

## Aşama 5 — Animasyon

Otomatik testler (`tests/adapter/test_animation.py`, `tests/c4d_real`) key ekleme/silme/değer/zaman, interpolasyon, key seçenekleri, tangent, track ayarları, yeni/silinen track, FPS ve zaman aralığı, kare presence'ı, oynatma sırasında güncelleme gitmemesini ve kilitli nesnelerin animasyonunu kapsar. Elle sadece arayüz davranışı denenir:

| # | Adım | Beklenen sonuç |
|---|---|---|
| 5.1 | 2023'te bir nesneyi kayıt (record) düğmesiyle keyframe'le, timeline'da birkaç kare ilerle, tekrar keyframe'le | R19'da aynı animasyon oynar |
| 5.2 | F-Curve editöründe bir tangent'ı fareyle çek | ~1 saniye içinde diğer tarafta da değişir |
| 5.3 | Bir tarafta animasyonu oynat (Play) | Diğer tarafın kendi karesi değişmez, panelde "frame N" akar. Log'da güncelleme seli yok |
| 5.4 | Project Settings'te FPS'i ve son kareyi değiştir | Diğer tarafta da değişir |

## Aşama 6 — Modelleme

Otomatik testler (`tests/adapter/test_modeling.py`, `tests/c4d_real`) nokta düzenleme (tek parça), topoloji değişimi (gerçek extrude), seçim tag'leri, UVW tag'leri, spline'lar (nokta, tangent, kapalı bayrağı), kilitler ve bozuk veri reddini kapsar. Elle sadece arayüz davranışı denenir:

| # | Adım | Beklenen sonuç |
|---|---|---|
| 6.1 | 2023'te bir küre/küpü "Make Editable" yap, Point modunda bir noktayı fareyle sürükle | R19'da nokta hareket eder (sürüklerken mi, bırakınca mı? not et) |
| 6.2 | Poligonları seçip Selection → Set Selection ile bir seçim tag'i oluştur | R19'da tag görünür, tag'e çift tıklayınca aynı poligonlar seçilir (≤ 1 sn gecikmeyle) |
| 6.3 | Extrude / Bevel / Knife kullan | R19'da aynı geometri ve UV'ler; log'da hata yok |
| 6.4 | UV Edit düzeninde UV'leri taşı | R19'da UV'ler değişir |
| 6.5 | Spline çiz (Pen tool), Bezier tangent'larını çek, kapat/aç | R19'da aynı (tangent için ≤ 1 sn gecikme) |
| 6.6 | 100.000+ poligonlu bir model üzerinde nokta taşı | Arayüz takılmaz (yavaş okuma koruması devrede), diğer tarafta güncellenir |

## Aşama 7 — Kim nerede, kim neye tıkladı

Otomatik testler geometri, kamera paylaşımı, nesne yaşam döngüsü ve çizim çağrılarını kapsar (`test_overlay.py`, `test_presence.py`). **Görüntüyü ancak sen doğrulayabilirsin:**

| # | Adım | Beklenen sonuç |
|---|---|---|
| 7.0 | **Tek pencerede hızlı kontrol:** panelde **Preview overlay** düğmesine bas, bir nesne seç | Nesnenin etrafında **turuncu kutu + "Preview" etiketi** ve önünde kamera ikonu. Görünmüyorsa çizim o pencerede çalışmıyor: `c4d_collab.log` içinde `Presence object registration ...` ve `Draw() is being called` satırlarına bak |
| 7.1 | İki C4D'yi yeniden başlat, 2023'te Host, R19'da Join | Panelde iki kullanıcı, her birinin yanında renk adı: `Ad [Blue]` |
| 7.2 | 2023'te bir nesneyi seç | **R19'da** o nesnenin etrafında 2023 kullanıcısının renginde bir kutu ve üstünde adı |
| 7.3 | 2023'te görünümü döndür/uzaklaştır | R19'da 2023'ün kamerası renkli bir kamera ikonu + adıyla görünür ve hareket eder |
| 7.4 | Seçimi bırak | R19'da kutu kaybolur |
| 7.5 | İkisi de farklı nesne seçsin | Her biri diğerinin nesnesini kendi renginde görür; renkler farklı |
| 7.6 | Dünya orijini ekran dışındayken (görünümü kaydır) | Kamera ikonları ve kutular **hâlâ görünür** (görünmüyorsa not et: C4D, sınır kutusu dışındaki nesneyi çizmeyebilir) |
| 7.7 | Object Manager'da | "C4D Collab Presence" nesnesi **görünmez**; oturumdan çıkınca sahneden silinir |
| 7.9 | **R19'da** bir küp ve silindir varken kaplamayı çalıştır | Nesnelerin **ön yüzleri görünür**, içi görünmez; diğer kullanıcının kamerası ve seçtiği nesnenin kutusu **kendi renginde** (beyaz değil) ve adıyla görünür |
| 7.10 | Birinin görünümü çevirmesini izle | Kamera ikonu **akıcı** kayar, 5 Hz'lik adımlar yok |
| 7.11 | Birinin seçili nesneyi sürüklemesini izle | Nesne **akıcı** hareket eder, kutu onunla birlikte kayar |
| 7.8 | İkinci bir ekran görüntüsü | Etiket metni okunaklı mı, çerçeve rengiyle eşleşiyor mu? |

## Sonuçlar

| Tarih | Sürüm(ler) | Geçen | Kalan / Notlar |
|---|---|---|---|
| 2026-10-25 | 2023.1.3 (host) + R19.024 (join), aynı PC | 2.1, 2.2 (2023), 2.3, 2.4, 2.5, 2.6, 2.7 | Python 2.7 ve 3.9 aynı oturumda ✅. R19'da ikinci kez Host'a basınca "port kullanımda" hatası doğru çıktı, ama mesaj Windows'un ham metniydi → anlaşılır bir mesajla değiştirildi. |
| 2026-10-25 | 2023.1.3 + R19.024 | 2.8, 2.9, 2.10, 2.11, 2.12 | **Aşama 2 tamam.** Log ve ayar dosyalarında şifre yok (`grep` ile doğrulandı). C4D kapanırken oturum temiz kapandı ("Cinema 4D is closing"). Açık kalan tek nokta: R19'da şifre alanı yıldızlı mı (önemsiz) |
| 2026-10-26 | Gerçek C4D, otomatik senaryo (`tests/c4d_real`) | 2023↔2023 17/17, 2023 host→R19 misafir 17/17, R19 host→2023 misafir 17/17 | R19 host testi **güvenlik açığı** buldu: R19'da `c4d.Opython` sabiti yok, Python generator'lar tanınmıyordu → sayısal ID + tip adı kontrolüyle düzeltildi, yeniden test edildi ✅ |
| 2026-10-26 | Gerçek C4D, otomatik senaryo + kilitler | 2023↔2023 26/26, 2023 host→R19 misafir 26/26, R19 host→2023 misafir 26/26 | **Aşama 4 otomatik kısmı tamam.** Elle kalanlar: 3.3 (fareyle sürükleme), 4.1–4.6 (fare/menü davranışı) |
| 2026-10-26 | Gerçek C4D, otomatik senaryo + animasyon | 2023↔2023 34/34, 2023 host→R19 misafir 34/34, R19 host→2023 misafir 34/34 | **Aşama 5 otomatik kısmı tamam.** Animasyonlu bir vektörün sadece X bileşeni animasyonluysa Y/Z hâlâ senkronize olur (testin bulduğu hata düzeltildi). Elle kalanlar: 5.1–5.4 (fareyle kayıt, tangent çekme, oynatma) |
| 2026-10-26 | Gerçek C4D, otomatik senaryo + modelleme | 2023↔2023 51/51, 2023 host→R19 misafir 51/51 | **R19 host→2023 misafir henüz çalıştırılmadı** (R19'un seçim/UV/spline *okuma* tarafı yani `GetSegments`/`GetRange` yedek yolu ve `GetSlow` bu yönde test edilir). Komut: `py tests/c4d_real/run.py --manual-peer host --label r19`, sonra R19'da Self-Test. Ölçümde bulunan ve düzeltilen: topoloji değişince seçim tag'inin boyutu ayrı pakette geç gidiyordu |
| 2026-10-27 | Gerçek C4D, otomatik senaryo + modelleme (UV düzeltmesi sonrası) | 2023↔2023 51/51, 2023 host→R19 misafir 51/51, **R19 host→2023 misafir 51/51** | **Aşama 6 otomatik kısmı tamam, üç kombinasyon da geçti.** R19 host testi gerçek bir boşluk buldu: R19'da yapılan UV düzenlemesi 2023'e ulaşmıyordu (en olası neden: R19 UV düzenlemesinde sayaç artırmıyor, doğrudan doğrulanmadı). UV tag'leri periyodik içerik kontrolüne alındı, düzeltme R19'da doğrulandı. Elle kalanlar: 6.1–6.6 |

## Aşama 8 - Malzemeler ve dokular

| # | Adım | Beklenen |
|---|------|----------|
| 8.1 | Host'ta dokulu malzemeleri olan bir proje aç, oturum kur, misafir katıl | Misafirde nesnelerin dokuları ve malzeme renkleri **host ile aynı** görünür |
| 8.2 | Host'ta bir malzemenin rengini / doku dosyasını değiştir | Misafirde birkaç saniye içinde değişir |
| 8.3 | Misafirde yeni bir malzeme oluştur ve nesneye sürükle | Host'ta malzeme ve doku etiketi belirir |
| 8.4 | Farklı bilgisayarda misafir (doku dosyası yok) | Dokular yine görünür (önbellekte: Cinema 4D tercih klasörü / c4d_collab_assets) |
| 8.5 | Büyük (25 MB üstü) bir doku | Gönderilmez, günlükte uyarı; diğer şeyler çalışır |

## Aşama 9 - Karakter rigi ve büyük sahne

| # | Adım | Beklenen |
|---|------|----------|
| 9.1 | Rigli karakterli proje, host oturum açar, misafir katılır | Karakter misafirde de aynı biçimde deforme olur (ağırlıklar, Skin, eklemler) |
| 9.2 | Host paneli / günlüğü | "Not shared (stays local): ..." satırı XPresso/Python'u sayar |
| 9.3 | Büyük sahnede bir nesneyi sürükle (iki pencerede) | Hareket akıcı; kamera ikonları akıcı; Cinema 4D donmaz |
| 9.4 | Yüzü Pose Morph'lu karakter, host oturum açar, misafir katılır | Misafirde yüz pozları (Pose Morph) var, slider'lar host ile aynı; yüz host'takiyle aynı görünür |
| 9.5 | Host'ta yüz slider'ını (veya XPresso kontrolcüsünü) oynat | Misafirde yüz aynı ifadeyi alır; misafirin **temel** mesh'i bozulmaz, günlükte geometri seli yok |
| 9.6 | Host'ta Pose Morph'u Edit moduna al, bir pozu seç, fırçayla/noktalarla şekillendir, sonra başka poza geç | Şekillendirme sırasında misafirin mesh'i değişmez; poz değişince yeni şekil misafire gider |
| 9.7 | R19'da `tests/c4d_manual/probe_posemorph.py` (Script Manager) | Rapor `%TEMP%/c4d_collab_probe_posemorph.txt`; API ve sayaç davranışı 2023 ile karşılaştırılır |

## Aşama 8 - MCP sayfası (Edit > Preferences > MCP)

Hem 2023'te hem R19'da yapılır. Cinema 4D'yi kapatıp açın (kaynak dosyaları yeniden okunur).

| # | Adım | Beklenen |
|---|------|----------|
| 10.1 | Cinema 4D'yi aç, konsola / Python konsoluna bak | `C4D Collab 0.1.0 loaded` yazar, **"without the MCP page" yazmaz** |
| 10.2 | Edit > Preferences, soldaki liste | **MCP** satırı var (Scheme Colors'ın altında olması beklenir; değilse nerede olduğunu yaz) |
| 10.3 | MCP sayfasını aç | 4 bölüm: **Server** (Allow MCP Server, Status, Host, Port, Auth Token, Show Token, Copy Token / Regenerate Token düğmeleri, Request Timeout), **Clients** (MCP Client açılır listesi, Client Status, Config File, Update Selected Client / Remove from Client / Copy Config düğmeleri, uyarı metni), **Security** (3 kutu), **Toolset** (4 kutu). Server kapalı: Allow MCP Server dışındaki her şey soluk |
| 10.4 | Allow MCP Server'ı işaretle | Onay sorusu çıkar. **Hayır**: kutu kapalı kalır. **Evet**: Status "Listening on 127.0.0.1:47921", Auth Token `****...` + son 4 karakter, alanlar canlanır (Host, Auth Token, Status, Client Status, Config File soluk kalır) |
| 10.5 | **Show Token**'ı işaretle / kaldır | Token tam görünür / yine maskelenir |
| 10.6 | **Copy Token**'a bas, bir metin alanına yapıştır | 32 karakterlik token yapışır; durum çubuğunda "Access token copied" |
| 10.7 | **Regenerate Token**'a bas | Token değişir (son 4 karakter başka) |
| 10.8 | MCP Client = Cursor (yüklüyse), **Update Selected Client** | Dosya yolunu ve "yedeklenir" notunu gösteren soru; Evet → "configuration updated" + yeniden başlatma notu; Client Status "Set up"; `~/.cursor/mcp.json` içinde `cinema4d` girdisi, yanında `.bak-...` yedeği. Sonra **Remove from Client** → "Not set up" |
| 10.9 | MCP Client = Claude Desktop, Update Selected Client | Store sürümünde yol `...Packages\Claude_...\LocalCache\Roaming\Claude\claude_desktop_config.json`; Evet → yazılır; Claude Desktop'ı **tamamen kapatıp** açınca araçlarda `cinema4d` görünür |
| 10.10 | MCP Client = Claude Code, Update Selected Client | Komut panoya kopyalanır ve diyalogda gösterilir; bir terminale yapıştırınca `claude mcp list` içinde `cinema4d` |
| 10.11 | MCP Client = Other, **Copy Config** | Update/Remove soluk; Copy Config panoya `mcpServers` JSON'u koyar |
| 10.12 | AI uygulamasından `ping` çağır | Cinema 4D sürümü ve Python sürümü döner |
| 10.13 | Security: Allow Python Execution kapalıyken `run_script`, sonra açıp tekrar | Önce "switched off" hatası, sonra çalışır |
| 10.14 | Toolset: Hierarchy'yi kapat, AI'dan `get_hierarchy` çağır | "Hierarchy toolset is switched off" (araç listesinden kalkması için AI uygulamasını yeniden başlat) |
| 10.15 | Request Timeout'u 1'e getir, Cinema 4D'yi meşgul et (`run_script` ile `time.sleep(5)`), bu sırada başka bir komut çağır | Meşgul komut bitince diğeri "timed out" döner, **sonradan çalışmaz** |
| 10.16 | Port'u değiştir (örn. 47922) | Status yeni porta geçer; Collab (Host/Join) aynen çalışır |
| 10.17 | Allow MCP Server'ı kapat | Status "Off"; `c4d_collab_mcp.json` silinir; her şey soluk |
| 10.18 | Cinema 4D'yi kapat/aç (sunucu açıkken) | Sunucu kendiliğinden döner, **soru sorulmaz** |
| 10.19 | Başka bir program aynı portu tutarken aç | Status "Error: port ..." gösterir, Cinema 4D çalışmaya devam eder |
| 10.20 | **Düğmeler hiçbir şey yapmıyorsa** | Çok önemli: "düğmelere basınca bir şey olmuyor" diye bildir (`Message` PreferenceData'da gelmiyor demektir; başka bir yol denenecek) |
