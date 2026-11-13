# Cinema 4D için Canlı Ortak Çalışma Eklentisi: Fizibilite Raporu

Tarih: 2026-10-24

İncelenen kaynak kodlar (`research/repos/` altında):
- **Multi-User** (slumber, GitLab, son commit 2026-09-01) ve çekirdek kütüphanesi **replication 0.10.3**
- **Mixer** (Ubisoft, V-Sekai fork'u, son commit 2025-09-01)
- **Meerkat** (arryllopez, son commit 2026-04-29)

> ⚠️ Üç projenin lisansı da **GPL**. Kodlarını kopyalayıp ücretli veya kapalı kaynaklı bir ürüne koyamayız. Fikirlerini ve mimarilerini öğrenip **kendi kodumuzu sıfırdan yazmamız** gerekiyor.

---

## 1. Blender'da nasıl yapmışlar?

Üç proje de aynı 5 temel problemi çözüyor. Her birinin çözümü:

### 1.1 Her nesneye kalıcı bir kimlik (ID) vermek
Ağ üzerinden "şu nesne değişti" diyebilmek için her nesnenin isimden bağımsız, değişmeyen bir ID'si olmalı.
- **Multi-User:** Blender'daki her ID tipine bir `uuid` alanı ekliyor (`bpy.types.ID.uuid = StringProperty`, `multi_user/__init__.py:49`). Bu alan .blend dosyasıyla birlikte kaydediliyor.
- **Meerkat:** Sunucu tarafında `object_id` olarak UUID tutuyor.

### 1.2 Değişikliği algılamak
- **Multi-User:** `depsgraph_update_post` olayını dinliyor (`handlers.py: on_scene_update`). Blender burada hangi nesnenin değiştiğini söylüyor ama neyin değiştiğini söylemiyor. Bu yüzden nesnenin tamamını bir sözlüğe (dict) döküyor (`dump`), sonra son gönderilen hâliyle **deepdiff** kütüphanesiyle karşılaştırıp sadece farkı (`Delta`) çıkarıyor (`replication/protocol.py: compute_delta`).
- **Mixer:** Aynı olayı dinliyor ama farklı bir yöntemle: tüm `bpy.data`'nın bellekte bir kopyasını tutuyor (`BpyDataProxy`), değişiklik gelince bu kopyayla karşılaştırıyor. JSON ile kodluyor. Kendi belgelerinde de belirttikleri gibi pratikte veri bloğunu bütün olarak gönderiyor.
- **Meerkat:** En basit yaklaşım. Sadece transform (konum, dönüş, ölçek) ve birkaç özellik için ayrı ayrı mesaj tipleri var (`update_transform`, `update_properties`...).

### 1.3 Kendi yansımasını engellemek (yankı döngüsü)
Uzaktan gelen değişikliği uyguladığında Blender bunu yeniden "değişiklik var" olarak bildiriyor. Önlem alınmazsa aynı değişiklik sonsuza kadar gidip gelir.
- **Multi-User:** Uygulanan nesnelerin UUID'lerini `applied_updates` listesine ekliyor, bir sonraki depsgraph olayında bunları atlıyor.
- **Mixer:** `skip_next_depsgraph_update` bayrağı kullanıyor.

### 1.4 Çakışmaları önlemek: kilit sistemi
**En önemli tasarım kararı bu.** Multi-User'ın yaklaşımı:
- Her nesnenin bir **sahibi** var: `RP_COMMON` (herkesin) veya bir kullanıcı adı.
- **Bir nesneyi seçtiğinde otomatik olarak kilitleniyor** (`timers.py: DynamicRightSelectTimer`, saniyede 10 kez kontrol ediyor). Seçimi bıraktığında kilit açılıyor.
- Başkasının kilitlediği nesneler seçilemez hâle getiriliyor (`hide_select`).
- Kullanıcının bağlantısı koparsa sunucu onun kilitlerini otomatik olarak açıyor (`server.py: disconnect_client`).
- Kilitler bağımlılıklara da yayılıyor (nesne kilitlenince mesh'i ve materyali de kilitleniyor).

**Mixer'da kilit yok.** Son yazan kazanıyor ve belgelerinde Undo/Redo'nun desteklenmediği, senkronizasyonu bozabileceği yazıyor. Bozulma riskinin bir kaynağı da muhtemelen bu.

### 1.5 Ağ ve sunucu
| | Multi-User | Mixer | Meerkat |
|---|---|---|---|
| Sunucu dili | Python (Blender'ın içinden ayrı bir süreç olarak başlatılıyor) | Python | **Rust** (Tokio + Axum) |
| Protokol | Ham TCP: önce ZeroMQ, artık sadece standart kütüphane (`socket` + `selectors`) | Ham TCP, kendi ikili formatı | **WebSocket** + JSON |
| Kodlama | `pickle` + deepdiff Delta | JSON | JSON |
| Yeni katılan kullanıcı | Sunucu tüm grafın anlık görüntüsünü (snapshot) gönderiyor | Odadaki **tüm mesaj geçmişi** baştan oynatılıyor | Sunucudaki durumu alıyor |
| İnternet üzerinden bağlantı | Port yönlendirme / VPN | **ZeroTier** veya port yönlendirme | Docker + **Caddy** (TLS) ile sunucuya kurulum |

Multi-User'ın ağ yapısı: iki kanal kullanıyor. **Komut** kanalı (kimlik doğrulama, kilit, silme) ve **veri** kanalı (nesne güncellemeleri). Bağlantının canlı olup olmadığını ayrı bir TTL (ping) süreci kontrol ediyor.

### 1.6 Thread kuralı
Blender'ın API'si thread-safe değil. Hepsi aynı deseni kullanıyor:
- Ağ işleri **arka plandaki bir thread'de** yürüyor.
- Gelen mesajlar bir **kuyruğa** (`queue.Queue`) konuyor.
- Ana thread'de bir zamanlayıcı (`bpy.app.timers`) kuyruğu boşaltıp değişiklikleri sahneye uyguluyor (Multi-User'da `ApplyTimer`, Meerkat'ta `websocket_client.py`).

### 1.7 Animasyon
Multi-User animasyonu **Action** veri bloğu olarak senkronize ediyor (`bl_types/bl_action.py`): her F-curve'ün keyframe noktalarını (konum, tutamaçlar, interpolasyon) **numpy** dizileri hâlinde döküyor. Keyframe tek tek değil, **curve'ün tamamı** gönderiliyor. Fark hesabı deepdiff'e bırakılmış.

### 1.8 Bilinen zayıflıklar
1. **Edit Mode:** Blender'da edit mode'daki mesh ayrı bir yapıda (`bmesh`) duruyor. İkisi de edit mode'dayken değişiklikleri **göndermiyor ve almıyor**, mod değişince toplu olarak gönderiyor (Multi-User'da `ContextError`).
2. **Undo:** Multi-User undo sonrası grafı yeniden çözüyor, Mixer hiç desteklemiyor.
3. **Performans:** Mesh değişince bütün mesh yeniden gönderiliyor. Büyük sahnelerde deepdiff yavaşlıyor.
4. **Sürüm uyumu:** Herkes aynı Blender ve eklenti sürümünü kullanmak zorunda.
5. **Bağlantı:** Kullanıcıların port yönlendirme/VPN ile uğraşması gerekiyor. Multi-User'da "server not found" şikayetleri bundan kaynaklanıyor.
6. **Veri bozulması:** İki proje de belgelerinde sahnenin bozulabileceği uyarısını yapıyor.

---

## 2. Cinema 4D'de bunu yapabilir miyiz?

**Kısa cevap: Evet.** Gereken her parça için C4D SDK'sında bir karşılık var. Bazı yerlerde Blender'dan daha kolay, bazı yerlerde daha zor.

### 2.1 Blender ve Cinema 4D karşılaştırması

| İhtiyaç | Blender | Cinema 4D | Durum |
|---|---|---|---|
| Eklenti dili | Python | **Python** (`.pyp`) ve **C++** SDK | ✅ |
| Kalıcı nesne ID'si | `ID.uuid` custom property | `BaseList2D.AddUniqueID(pluginID, bytes)` / `FindUniqueID`, ayrıca her öğenin `GeMarker`'ı (`MAXON_CREATOR_ID`) | ✅ Ama marker kopyalama ve yeniden yüklemede değişebilir, kendi ID'mizi `AddUniqueID` ile eklemeliyiz. |
| "Bir şey değişti" olayı | `depsgraph_update_post` (hangi ID'lerin değiştiğini söylüyor) | `EVMSG_CHANGE` core mesajı (**hiçbir şey söylemiyor**) | ⚠️ Daha zor |
| Neyin değiştiğini bulmak | deepdiff | Her nesnenin **dirty sayaçları**: `GetDirty(DIRTYFLAGS_DATA / MATRIX / ...)` ve `GetHDirty(HDIRTYFLAGS_OBJECT_HIERARCHY ...)`. Sayaç arttıysa o nesne değişmiş demek. | ✅ Hatta deepdiff'ten daha ucuz ve hızlı bir ön filtre |
| Sürükleme sırasında canlı güncelleme | Depsgraph sürekli tetikleniyor | `EVMSG_ASYNCEDITORMOVE` (MOVE_START/CONTINUE/END) ama **sadece GeDialog'a** geliyor | ⚠️ Eklentinin bir panel/dialog'u açık olmalı veya timer ile yoklama yapılmalı |
| Thread'den sahneyi değiştirmek | `bpy.app.timers` | `c4d.SpecialEventAdd(pluginID)` → `MessageData.CoreMessage` ana thread'de çalışır. C++'ta `maxon::ExecuteOnMainThread` var. | ✅ cinema4d-mcp projesi tam olarak bu deseni kullanıyor |
| Periyodik iş | `bpy.app.timers` | `MessageData.GetTimer()` | ✅ |
| Ağ | Python `socket` | Python 3 `socket`/`selectors`/`threading` (stdlib) çalışıyor. C++'ta maxon **network framework** var, WebSocket dahil (az belgelenmiş). | ✅ |
| Undo algılama | `undo_post` handler | `MSG_DOCUMENTINFO_TYPE_UNDO/REDO` (undo bittikten **sonra**). Python'da güvenilir değil, düzgün yolu **SceneHookData**, o da **sadece C++**. | ⚠️ |
| Animasyon verisi | Action / F-curve | `CTrack` → `CCurve` → `CKey` (`GetCTrackRoot`, `FindCTrack`, `InsertTrackSorted`) | ✅ Python'da tam erişim var |
| Modelleme (edit mode) | Ayrı `bmesh`, canlı senkronizasyon zor | C4D'de nokta/kenar/poligon modunda **doğrudan `PolygonObject` düzenleniyor**, ayrı bir edit kopyası yok | ✅ **Blender'dan daha kolay olabilir** (test edilmeli) |
| Kodu korumak (ticari) | Python kodu açık | `.pyp` dosyaları şifrelenebiliyor (`.pypv`), C++ derlenmiş | ✅ Satış için iyi |
| 3. parti Python paketleri | pip/wheel | `c4dpy -m pip` mümkün ama Maxon numpy gibi paketler için destek vermiyor | ⚠️ Mümkün olduğunca stdlib kullanmalıyız |
| Rakip | Multi-User, Mixer, Collab Pro | **Yok.** Sadece tek yönlü köprüler var (Plasticity Bridge, Rokoko, cinema4d-mcp). | ✅ Pazar boş |

### 2.2 C4D'ye özel en büyük iki zorluk

**1. Değişikliği algılamak (`EVMSG_CHANGE` hiçbir bilgi vermiyor).**
Çözüm: Takip edilen her nesne için son görülen dirty sayaçlarını sakla. `EVMSG_CHANGE` geldiğinde (ve ayrıca 50–100 ms'lik bir timer ile) sahneyi gez ve sayacı değişen nesneleri bul. Sadece onların parametrelerini döküp farkı hesapla. Bu iki aşamalı yöntem (ucuz sayaç kontrolü → pahalı fark hesabı) Multi-User'ın "her şeyi dök ve karşılaştır" yönteminden daha verimli.

**2. Python'un sınırları (SceneHook yok, sürükleme olayı sadece dialog'a geliyor).**
Çözüm: **Hibrit mimari.** Mantığın büyük kısmı Python'da yazılır, gerekirse küçük bir C++ modülü (SceneHook) undo/redo ve belge olaylarını yakalayıp Python'a iletir. MVP **tamamen Python** olabilir: undo'yu ilk sürümde "undo sonrası etkilenen nesneleri yeniden gönder" diye basitçe ele alırız.

### 2.3 Önerilen mimari

```
┌─────────────── Cinema 4D (her kullanıcı) ─────────────────┐
│  Python eklentisi (.pyp)                                   │
│   ├─ MessageData plugin                                    │
│   │    ├─ CoreMessage(EVMSG_CHANGE)  → değişiklik tarayıcı │
│   │    ├─ CoreMessage(SpecialEventAdd) → gelen kuyruğu uygula│
│   │    └─ GetTimer(~50ms)            → kilit / seçim / presence │
│   ├─ ChangeTracker: UUID ↔ BaseList2D, dirty sayaç önbelleği │
│   ├─ Serializers: Object, Tag, Material, CTrack/CCurve/CKey, │
│   │               PolygonObject (nokta/poligon dizileri)    │
│   ├─ GeDialog paneli (oturum, kullanıcılar, EVMSG_ASYNCEDITORMOVE) │
│   └─ Ağ thread'i: WebSocket istemcisi ↔ kuyruk             │
└────────────────────────────────────────────────────────────┘
                      │  WebSocket (TLS)
┌─────────────── Relay Sunucusu (bulutta) ───────────────────┐
│  Oturumlar/odalar, kimlik doğrulama, kilit yönetimi,       │
│  sahne anlık görüntüsü + işlem günlüğü, presence yayını    │
│  (Rust/Axum veya Node/Go, Docker + Caddy)                  │
└────────────────────────────────────────────────────────────┘
```

Blender projelerinden alacağımız fikirler:
- **Multi-User'dan:** seçince otomatik kilitleme, bağlantı kopunca kilitleri açma, bağımlılık grafı (nesne → materyal), yankı engelleme listesi, katılan kullanıcıya snapshot gönderme.
- **Mixer'dan:** presence (kimin hangi frame'de olduğu, hangi nesneleri seçtiği, kamera görüş alanı), odaların açık tutulması ve kaydedilmesi.
- **Meerkat'tan:** WebSocket + bulut relay (port yönlendirme derdi yok), basit ve tiplenmiş mesajlar.

Bizim farkımız olacak şeyler:
- **Animasyon odaklı ilk sürüm:** keyframe seviyesinde senkronizasyon (curve'ün tamamı değil, değişen `CKey`), track bazında kilit, timeline'da diğer kullanıcının oynatma imlecini görme.
- **Sürüm kontrolü:** bağlanırken C4D ve eklenti sürümü sunucuda kontrol edilir, uyumsuzsa bağlantı reddedilir.
- **Güvenlik ağı:** sunucu periyodik olarak `.c4d` yedeği tutar ve "oturumu geri yükle" seçeneği sunar (bozulma korkusu bu pazarın en büyük engeli).

### 2.4 MVP kapsamı (önerilen)
1. Oturum oluşturma ve katılma (bulut relay, oda kodu ile)
2. Nesne oluşturma, silme, yeniden adlandırma, hiyerarşi (parent/child)
3. Transform senkronizasyonu (sürükleme sırasında canlı)
4. Keyframe senkronizasyonu (`CTrack`/`CCurve`/`CKey`)
5. Seçince otomatik kilitleme, kilitli nesnelerin renkli gösterilmesi
6. Presence: kullanıcı listesi, kimin hangi frame'de olduğu
7. Katılan kullanıcıya sahnenin tamamını gönderme (snapshot)

MVP'ye alınmayacaklar: materyal ağaçları (Node Editor), MoGraph, Simulation, Redshift ayarları, büyük poligon modelleri, texture dosyaları. Bunlar 2. aşamaya kalır.

### 2.5 Riskler ve test edilmesi gerekenler (kod yazmadan önce)
1. **Dirty sayaçları:** Hangi işlem hangi sayacı artırıyor? Örneğin keyframe eklemek objenin `DIRTYFLAGS_DATA`'sını mı, `CTrack`'in kendi sayacını mı artırıyor? → Küçük bir test eklentisiyle ölçülmeli.
2. **`AddUniqueID` kopyalamada ne oluyor?** Nesne kopyalanınca ID de kopyalanıyorsa (büyük ihtimalle öyle), aynı ID'ye sahip iki nesneyi algılayıp yeni ID vermemiz gerekir.
3. **Python performansı:** 10.000 nesnelik bir sahnede dirty taraması kaç ms sürüyor?
4. **Uzaktan değişikliği uygulamak undo geçmişine giriyor mu?** `StartUndo/AddUndo` kullanmadan değiştirirsek kullanıcının Ctrl+Z'si başkasının işini geri alır mı?
5. **Lisans:** Kullanıcıların çoğunda C4D 2024–2026 var. Python API'nin hangi sürümünü minimum kabul edeceğimize karar verilmeli.

### 2.6 Sonuç
| Soru | Cevap |
|---|---|
| Teknik olarak mümkün mü? | **Evet.** Gereken tüm parçalar Python SDK'da var. Undo gibi ileri konular için küçük bir C++ modülü gerekebilir. |
| Blender'dan zor mu? | Değişiklik algılama daha zor (`EVMSG_CHANGE` bilgi vermiyor). Modelleme senkronizasyonu muhtemelen **daha kolay** (ayrı edit mode yok). |
| Rakip var mı? | **Yok.** |
| İlk adım | 1–2 günlük bir "spike" (deneme eklentisi): C4D'de dirty sayaçlarını ve UUID davranışını loglayan, iki C4D arasında yerel ağda transform ve keyframe gönderen minimal bir Python eklentisi. |

---

## Kaynaklar
- Multi-User kaynak kodu: https://gitlab.com/slumber/multi-user
- Mixer kaynak kodu: https://github.com/V-Sekai/mixer (doc/synchronization.md, doc/protocol.md, doc/hosting.md)
- Meerkat kaynak kodu: https://github.com/arryllopez/meerkat
- C4D Core/Event mesajları: https://developers.maxon.net/docs/py/2024_3_0/consts/MSG_CORE_EVMSG.html
- C4D thread kuralları: https://developers.maxon.net/docs/Cinema4DCPPSDK/page_manual_cinemathreads.html
- C4D BaseList2D (unique ID, CTrack): https://developers.maxon.net/docs/cpp/2026_1_0/page_manual_baselist2d.html
- Dirty sayaçları tartışması: https://developers.maxon.net/topic/11955/python-tag-matrix-is-always-dirty-or-vice-versa
- EVMSG_CHANGE'in bilgi vermemesi: https://developers.maxon.net/topic/14124/how-to-detect-a-new-light-and-pram-change
- MAXON_CREATOR_ID / GeMarker: https://developers.maxon.net/forum/post/62991
- Python'da SceneHook olmaması: https://developers.maxon.net/forum/post/59765
- Undo algılama: https://developers.maxon.net/forum/post/51089
- EVMSG_ASYNCEDITORMOVE sadece dialog'a gelmesi: https://developers.maxon.net/forum/post/49839 , https://developers.maxon.net/docs/cpp/25_113/page_manual_coremessages.html
- C++ WebSocket kullanımı: https://developers.maxon.net/forum/post/75503
- C4D Python kütüphaneleri: https://developers.maxon.net/docs/py/2025_1_0/manuals/manual_py_libraries.html
- SpecialEventAdd + socket deseni (cinema4d-mcp): https://github.com/ttiimmaacc/cinema4d-mcp
- WebSocket köprüsü örneği: https://github.com/ninsent/plasticity-c4d-plugin
