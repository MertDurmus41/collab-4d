# C4D Collab — Mimari

Bu belge projenin **anayasasıdır**. Yeni kod yazmadan önce buraya uyup uymadığına bak. Mimari değişirse önce bu belge güncellenir.

## 1. Hedef
Birden fazla kişinin aynı Cinema 4D sahnesinde gerçek zamanlı animasyon ve modelleme yapabilmesi.
- Ücretsiz, MIT lisanslı.
- Cinema 4D **R17 (2016) → 2026+** sürümleri desteklenir.
- Sunucu, **proje sahibinin (host) C4D'sinin içinde** çalışır. İsteğe bağlı olarak bağımsız Python/Docker sunucusu da kullanılabilir.

## 2. Katmanlar

```
┌──────────────────────── Cinema 4D (her kullanıcı) ───────────────────────────┐
│  c4d_collab.pyp  (giriş noktası, plugin kayıtları)                           │
│  ┌── UI (c4d'ye bağlı) ───────────────────────────────────────────────────┐ │
│  │ CollabDialog: Host/Join, kullanıcılar, durum, log                      │ │
│  │ PresenceDrawer: diğer kullanıcıların kilit renkleri, isimleri, kameraları│ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│  ┌── Adaptör: c4d_collab_lib/c4d_adapter (c4d'ye bağlı) ──────────────────┐ │
│  │ plugin_ids · compat · bridge · ui_dialog                               │ │
│  │ (Aşama 3+: ids · tracker · serializers/* · applier · reconcile)       │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│  ┌── Çekirdek: c4d_collab_lib/core (saf Python, c4d IMPORT ETMEZ) ────────┐ │
│  │ protocol · net · model · diff · locks · client · mirror · session ·   │ │
│  │ server · compat · log                                                 │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
        ▲  TCP: [4 bayt uzunluk][1 bayt bayrak][UTF-8 JSON (zlib opsiyonel)]
        ▼
  Host C4D içindeki core.server  ── veya ──  server/run_server.py / Docker
```

### Değişmez kurallar
1. `core/` **asla** `c4d` import etmez. Bu sayede C4D olmadan test edilebilir, aynı sunucu her yerde çalışır ve ileride Maya/Mine-imator için sadece yeni bir adaptör yazmak yeter.
2. Tüm eklenti kodu **Python 2.7 ve 3.x** ile çalışır: f-string, tip ipuçları, `async`, `nonlocal`, `pathlib`, `selectors` kullanılmaz. Her dosya `from __future__ import absolute_import, division, print_function, unicode_literals` ile başlar. ASCII dışı karakter (ör. Türkçe harf) içeren dosyalar ilk satırda `# -*- coding: utf-8 -*-` taşır, yoksa Python 2.7'de dosya açılmaz.
3. Sadece **standart kütüphane** kullanılır (pip yok, `pickle`/`eval` yok).
4. Sahne **yalnızca ana thread'den** değiştirilir. Ağ thread'i sadece kuyruğa yazar ve `SpecialEventAdd` ile ana thread'i uyandırır.
5. Uzaktan gelen değişiklikler undo geçmişine yazılmaz.
6. Sürüme bağlı her C4D API'si `c4d_adapter/compat.py` içinden çağrılır.

## 3. Veri modeli (`core/model.py`)
- **Node:** `uuid, kind (object|tag|track|doc), ctype, parent, prev, name, data`
  - Hiyerarşi çocuğun özelliğidir: `parent` (ebeveyn uuid'si veya `null` = kök) ve `prev` (önceki kardeşin uuid'si veya `null` = ilk). Tag ve track'lerin `parent`'ı sahibi olan nesnedir.
- **Op (işlem):** `{"op": "create"|"update"|"delete"|"move", "uuid": ..., ...}`
  - `create`: `kind, ctype, parent, prev, name, data`
  - `update`: `patch` (bkz. `core/diff.py`), istenirse `name`
  - `move`: `parent, prev`
  - `delete`: alt düğümlerle birlikte siler
- **Sıra:** Sunucu her kabul ettiği Op'a artan bir `seq` verir ve herkes Op'ları bu sırayla uygular.

## 4. Protokol (`core/protocol.py`)
Çerçeve: `>I` uzunluk + `B` bayrak (bit0 = zlib) + JSON. Maksimum mesaj boyutu 64 MB.

| Yön | Mesaj | Alanlar |
|---|---|---|
| S→C | `challenge` | `nonce, session, auth` |
| C→S | `hello` | `name, proof, client{app, app_version, python, plugin}` |
| S→C | `welcome` | `user_id, users, locks, seq` |
| S→C | `snapshot_begin` / `snapshot_chunk` / `snapshot_end` | `count` / `ops` / `seq` |
| C→S | `ops` | `cid, ops[]` |
| S→C | `ops` | `author, ops[]` (her op'ta `seq` var) |
| S→C | `ack` | `cid, seq, accepted[index], rejected[{i, uuid, reason}]` |
| C→S | `lock` / `release` | `uuids[]` (`release` için `all: true` da olabilir) |
| S→C | `locks` | `changes{uuid: user_id \| null}` |
| S→C | `lock_denied` | `uuids[]` |
| C→S / S→C | `presence` | `data{frame, selection, camera}` / `user, data` |
| S→C | `users` | `users[]` |
| ↔ | `ping` / `pong` | `ts` |
| ↔ | `bye` | `reason` |
| S→C | `error` | `code, message` |

Her mesajda `v` (protokol sürümü) alanı bulunur. Kimlik doğrulama: `proof = HMAC-SHA256(şifre, nonce)`. Şifre ağ üzerinden hiç gönderilmez.

Büyük sayı dizileri `protocol.pack_floats` / `pack_ints` ile (little-endian `struct` + base64) taşınır.

## 5. Kilitler (`core/locks.py`)
- Seçince kilitleme: istemci, seçtiği nesneler için `lock` mesajı gönderir. Kilit, tüm alt düğümleri (çocuk nesneler, tag'ler, track'ler) de kapsar.
- Bir düğümü düzenleme izni: düğüm kilitsizse **veya** kilit sende ise. Ata zincirindeki kilitler de geçerlidir.
- Sunucu yetkisiz Op'ları reddeder. İstemci tarafında `reconcile` modülü yerel değişikliği cache'teki doğru hâle geri döndürür.
- Bağlantısı kopan kullanıcının tüm kilitleri sunucu tarafından açılır.

## 6. Oturum
- Host: kendi C4D'sinde `core.server` başlatır ve **kendi sunucusuna normal bir istemci gibi** `127.0.0.1` üzerinden bağlanır. Sahnesini `create` Op'ları olarak yükler (seed). Tek bir kod yolu olduğu için özel durum yoktur.
- Join: `welcome` + snapshot alır, sahneyi **yeni bir belgede** kurar.
- Host'un C4D'si kapanırsa oturum biter.

### İstemci tarafı (`core/client.py`, `core/mirror.py`)
- `CollabClient`: Soketi arka plandaki bir thread'de çalıştırır. Ana thread `send_ops / lock / release / presence` ile mesaj gönderir ve `poll()` ile gelen mesajları alır. Yeni mesaj gelince `notify` çağrılır (C4D'de bu `SpecialEventAdd` olur).
- `SessionMirror`: Sunucunun **onayladığı** durumun kopyasıdır: snapshot + diğer kullanıcıların Op'ları + bizim onaylanmış (ack) Op'larımız. Adaptör, canlı sahneyi bununla karşılaştırarak yerel değişiklikleri bulur. Reddedilen düzenlemeler de bu kopyaya göre geri alınır.
- Sunucu tüm mesajları tek bir soket üzerinden ve `seq` sırasıyla gönderdiği için her istemcinin kopyası aynı sırayla güncellenir.

## 6b. Güvenlik
Bir oturum, başkasının bilgisayarına ağ üzerinden veri göndermek demektir. Kurallar:

| Tehdit | Önlem | Nerede | Test |
|---|---|---|---|
| Uzaktan kod çalıştırma | `pickle`/`eval` yok, sadece JSON. Python kodu taşıyan C4D öğeleri (Python tag/generator/effector/field, XPresso Python node) **varsayılan olarak senkronize edilmez** (Aşama 3+) | protocol, serializers | test_protocol |
| Şifre sızması | Şifre ağdan gönderilmez (HMAC-SHA256 challenge). Diske ve log'a yazılmaz, kullanıldıktan sonra panelden silinir | protocol, ui_dialog | test_protocol, test_adapter |
| Şifresiz oturum | Host olmak için en az 4 karakterli şifre zorunlu | core/session | test_controller |
| Şifre deneme saldırısı | Bir IP'den 60 sn içinde 5 hatalı girişte o IP 60 sn engellenir | server | test_security |
| Bellek doldurma | Giriş yapmamış bağlantıya en fazla 64 KB'lık mesaj, girişten sonra 64 MB. Sıkıştırılmış veride açılmış boyut da sınırlı (zip bombası) | protocol, server | test_security, test_protocol |
| Mesaj seli | Bağlantı başına saniyede 1000 mesaj (ani yük için 3000) | server | test_security |
| Okumayan istemci | Bekleyen çıktı 128 MB'ı aşarsa bağlantı kesilir | server | test_security |
| Bozuk/kötü niyetli veri | Her Op ve mesaj alanı doğrulanır. Hatalı Op tek tek reddedilir, sunucu çökmez | model, server | test_model, test_security |
| Başkasının işini bozma | Kilit sistemi, sunucu tarafında yetki kontrolü | locks, server | test_session |
| Arayüz/log enjeksiyonu | Kullanıcı adlarından kontrol karakterleri temizlenir, ad 64 karakterle sınırlı | server | test_security |
| Bağlantı gizliliği | Trafik **şifrelenmez**. İnternet üzerinden kullanım için Tailscale/ZeroTier (şifreli VPN) önerilir. Opsiyonel TLS, Aşama 8'de değerlendirilecek | belgeler | — |
| Yerel ağda istenmeyen erişim | Windows Güvenlik Duvarı sorduğunda sadece "Özel ağlar" seçilmesi önerilir | belgeler | elle |

## 7. Adaptör (C4D) — sahne senkronizasyonu (Aşama 3)

```
 yerel düzenleme ──► tracker.scan() ──► Op'lar ──► session.send_ops() ──► sunucu
                        │  (shadow'a hemen uygulanır)
 sunucu ──► mirror ──► "remote_ops" ──► sync.apply_remote():
                                         1) önce tracker.scan() (kendi değişikliklerimizi gönder)
                                         2) applier.apply() → shadow + C4D sahnesi
                                         3) dokunulan atomların dirty değerleri + belge baseline'ı kaydedilir (yankı yok)
```

- `ids.Registry`: uuid ↔ canlı atom. Kimlik `AddUniqueID(UNIQUE_ID_APP, 16 bayt)` ile saklanır. Aynı uuid bir taramada iki kez görülürse ikinciye yeni uuid verilir.
- `tracker.Tracker`: Belge sayaçları değişmediyse hiçbir şey yapmaz. Değiştiyse nesne ağacını gezer: yapı (`parent/prev`) shadow ile karşılaştırılır, sadece kendi `DATA/MATRIX` sayacı değişen atomlar serileştirilir. Sadece `MATRIX` değiştiyse sadece transform parametreleri okunur (sürükleme sırasında hızlı yol). Op sırası: oluşturma/taşıma/güncelleme ağaç sırasıyla (ebeveyn önce), silmeler en sonda.
- `applier.Applier`: Önce shadow'a uygular (doğrulama), sonra sahneye. Bir nesnenin verisi yüklenemezse (bozuk geometri vb.) nesne yine oluşturulur, hata log'a yazılır.
- `sync.SceneSync`: Host'ta snapshot gelince aktif belge oturum belgesi olur ve tüm sahne gönderilir (seed). Misafirde snapshot **yeni bir belgede** kurulur. **Belge değişince ne olur** (`SceneSync._usable`, `SWITCH_GRACE` = 10 sn): oturum başlarken açık olan belgeler hatırlanır. (1) **Yeni bir proje açılırsa** (File > Open/New: o an yokmuş bir belge etkin olur) kullanıcı oturumdan **hemen ayrılır**; misafir için yalnızca kendi bağlantısı kapanır ve diğerleri kullanıcı listesinden düşer, **host için oturum herkes için biter** (sunucu host'un Cinema 4D'sinde çalışır) ve misafirlere bildirilir; panel günlüğüne nedeni yazılır ("You left the session: you opened another project" / "The session ended: ..."). (2) Oturum başlamadan önce de açık olan **başka bir sekmeye geçilirse** senkronizasyon durur ("paused", 10 sn içinde dönülmezse ayrılır); geri dönünce kaldığı yerden devam eder: kazara bir sekme geçişi oturumu öldürmesin diye. (3) **Oturum belgesi kapatılırsa** ayrılır. Ayrıldıktan sonra yeniden katılmak mümkündür (yeni belge kurulur).
- `bridge`: MessageData plugin'i. `SpecialEventAdd`, 20 ms timer (iki zamanlayıcı en az 12 ms arayla çalışır) ve `EVMSG_CHANGE` ile tetiklenir. Panel açıksa `EVMSG_ASYNCEDITORMOVE` ile sürükleme sırasında da tarama yapılır.

### Node verisi
| kind | data |
|---|---|
| `object` | `{"p": {param anahtarı: değer}, "g": {...}}` (`g` sadece poligon nesnelerinde) |
| `tag` | `{"p": {...}}` |

- **Parametre anahtarı:** DescID'nin tamamı, `"id:dtype:creator/id:dtype:creator"` biçiminde (user data için iki seviye gerekir).
- **Değerler:** sayı/bool/metin olduğu gibi; `{"V": [x,y,z]}` vektör/renk; `{"T": [pay, payda]}` BaseTime; `{"L": uuid}` senkronize edilen başka bir nesneye link.
- **Desteklenen tipler:** Real, Long, Bool, String, Filename, Vector, Color, Time, Link. Diğerleri (spline, gradient, in/exclude...) şimdilik gönderilmez.
- **Hariç:** isim (ayrı gider), katman linki, `ABS_*`/`GLOBAL_*` (türetilmiş değerler).
- **Parametreler hiç silinmez:** Patch'lerde parametre için `del` gönderilmez. Farklı C4D sürümleri farklı parametrelere sahip olabilir; eski sürüm yeni sürümün parametrelerini silmemeli.
- **Geometri (`g`):** `{"n": [nokta, poligon], "pts": pack_floats, "polys": pack_ints}`. Alıcıda indeks aralığı ve boyutlar doğrulanır. Aşama 3'te her değişiklikte geometrinin tamamı gönderilir; Aşama 6'da sadece değişen noktalar gönderilecek.

### Tip politikası (`serializers/nodes.py`)
| Grup | Tipler | Davranış |
|---|---|---|
| Kod taşıyan | Python generator/effector/field/tag, COFFEE, **Python/COFFEE düğümü içeren** XPresso | **Hiç gönderilmez, ağdan gelirse hiç oluşturulmaz.** (Standart düğümlerden oluşan XPresso grafiği veri sayılır ve gönderilir, bkz. "XPresso grafikleri".) Eklentinin kendi "Collab rig copy" Python tag'i yalnız bu bilgisayarda kalır, bu kural gereği hiç gönderilmez. Gelen nesne yerine boş bir Null konur. Tanıma iki katmanlıdır: sayısal ID'ler (R19'da `c4d.Opython` gibi sabitler yok!) **ve** tip adında "Python/COFFEE/XPresso" geçmesi |
| Henüz desteklenmeyen tag'ler | Vertex map, normal ve gizli veri tag'leri (UVW ve seçim Aşama 6'da, Weight ve Pose Morph Aşama 8'de desteklendi) | Gönderilmez |
| Bu sürümde olmayan tip | Yeni C4D'den gelen nesne, kurulu olmayan plugin nesnesi | Hiyerarşi bozulmasın diye yerine Null konur. Bu Null'ın verisi **hiç geri gönderilmez**, aslının üzerine yazılmaz |

## 7a. Kilitler ve reconcile (Aşama 4)

```
seçim değişti ──► sync._sync_selection(): yeni seçilenler → LOCK, bırakılanlar → RELEASE,
                  presence.selection güncellenir (session.set_presence alanları birleştirir)
başkasının kilidi ──► seçim geri alınır (DelBit) + durum çubuğu/log: "X, Y tarafından düzenleniyor"

tracker.scan():   kilitli düğümde değişiklik → GÖNDERİLMEZ
                    • parametre/isim değişti, ebeveyn değişti, silindi → tracker.restore
                    • kilitli ebeveynin altına yeni nesne/tag → tracker.discard
                    • sadece sıra değişti → yok sayılır (kardeş taşınınca da olur)
                  hiyerarşi değiştiyse önce hızlı bir ID taraması: izinsiz silme varsa
                  önce geri yüklenir, sonra karşılaştırma yapılır (yan etkili sıra Op'u gitmez)
applier.restore(): shadow'daki hâle döndür (silindiyse alt ağacıyla yeniden oluştur)

sunucu reddetti (yarış) ──► "rejected" ──► applier.reconcile(mirror.scene, uuid):
                  shadow + sahne, sunucunun onayladığı hâle (mirror) getirilir
lock_denied / başkası kilidi aldı ──► seçim bırakılır
```

- **Yetki kuralları sunucuyla aynıdır:** Düzenleme için düğüm ya da atalarından biri başkasının kilidinde olmamalı (`mirror.editable`). Silme/taşıma için ayrıca hiçbir alt düğüm başkasının kilidinde olmamalı (`mirror.subtree_editable`).
- **Ctrl+Z:** Başkasının kilitli nesnesini geri almak da bir "yerel değişiklik"tir: algılanır, gönderilmez ve geri yüklenir. SceneHook (sadece C++) gerekmez.
- **Görünürlük:** Panelde "Being edited by others" listesi var. Viewport'ta renkli gösterim Aşama 7'de gelecek.

## 7c. Animasyon (Aşama 5)

| Node | uuid | data |
|---|---|---|
| `track` (parent = animasyonlu nesne/tag) | `md5(sahip uuid \| parametre anahtarı)`: herkes aynı ID'yi hesaplar, track'e ID yazmak gerekmez | `{"d": parametre anahtarı, "p": track ayarları (before/after döngü, offset...), "k": {"<saniye>": key}}` |
| `doc` | `"document"` | `{"fps", "min", "max", "lmin", "lmax"}` (saniye) |

- **Key:** `{"v": değer, "i": interpolasyon, "tl"/"tr": tangent zamanları, "vl"/"vr": tangent değerleri, "q": {seçenek id: değer}}`. Seçenekler (auto tangent, clamp, break, mute, weighted...) ID ile doğrudan okunur; her key için description gezmek yavaş olurdu.
- Key'ler **zamana göre** anahtarlanır. Böylece patch sadece değişen key'leri taşır. Uygulama tarafında eğri baştan kurulur (`FlushKeys` + `AddKey`), çünkü bu basit ve kesin, eğriler de küçük.
- **Sadece değer track'leri (`CTbase` = 5350)** senkronize edilir. PLA, ses ve morph track'leri v1 sonrasına kaldı.
- **Değişiklik algılama:** Belgenin `ANIMATION` sayacı (R19'da `ALL`) değişince track'ler taranır. Ölçümlere göre **tangent ve track ayarı değişiklikleri hiçbir sayacı artırmıyor**, bu yüzden ayrıca her `TRACK_SCAN_INTERVAL` (1 sn) içerik karşılaştırması yapılır. Yankı olmaması için karşılaştırma shadow ile değil, "son görülen/uygulanan canlı içerik" önbelleğiyle yapılır.
- **Animasyonlu parametreler düz değer olarak gönderilmez.** Yoksa oynatma sırasında her karede güncelleme giderdi. Sadece bir vektörün X'i animasyonluysa X `null` gönderilir (alıcı kendi değerini korur), Y/Z normal senkronize olur.
- **Geçerli kare (playhead) herkesin kendisine aittir.** Senkronize edilmez, sadece `presence.frame` olarak paylaşılır (saniyede en çok 5 kez). Panelde "frame N" olarak görünür.
- **Kilitler track'lere de uygulanır:** Track'in ebeveyni nesnedir. Kilitli bir nesnenin key'lerini değiştirmek geri alınır, ona yeni track eklemek kaldırılır.

## 7d. Modelleme (Aşama 6)

Geometri ve tag içerikleri **parçalara (chunk) bölünerek** taşınır: her 128 nokta/poligon ayrı paketlenir, genel `diff` sadece değişen parçaları gönderir. 100.000 noktalı bir modelde tek bir noktayı taşımak ~4 KB'lık tek bir parça gönderir (test: `test_modeling.py`).

| Veri | Yer | Biçim |
|---|---|---|
| Poligon nesnesi | `object.data["g"]` | `{"n": [nokta, poligon], "pts": {parça: float x3}, "polys": {parça: int x4}}` |
| Spline | `object.data["g"]` | `{"n": [nokta, segment], "pts": {...}, "seg": int (adet, kapalı)*, "tan": {parça: float x6}}` (tangent sadece varsa) |
| Seçim tag'i (nokta/poligon/kenar) | `tag.data["s"]` | `{"n": eleman sayısı, "r": int (ilk, son)* kapalı aralıklar}` |
| UVW tag'i | `tag.data["uv"]` | `{"n": poligon sayısı, "uv": {parça: float x8}}` (a-d köşelerinin u,v değerleri) |

- **Sayı değiştiyse tam yükleme, değişmediyse kısmi:** `n` aynıysa sadece değişen parçalar yazılır (`SetPoint`/`SetSlow`). `n` değiştiyse (extrude, kesme...) nesne `ResizeObject` ile baştan kurulur.
- **Bağımlı tag'ler:** Topoloji değişince C4D, UVW tag'ini kendisi yeniden boyutlandırır. Alıcı tarafta nesne yüklendikten sonra seçim ve UVW tag'leri shadow'dan yeniden uygulanır (`Applier._reload_dependents`). Gönderen tarafta sahibinin eleman sayısı değişince **UVW ve seçim tag'leri o taramada hemen yeniden okunur**, böylece topoloji değişikliği tek bir paket olarak gider.
- **Sayaç hareket ettirmeyen içerik**: seçim tag'i düzenlemeleri, spline tangent ve segment bayrakları (2023'te ölçüldü) ve **UVW düzenlemeleri** (2023'te sayaç artıyor ama R19'da UV düzenlemesi karşıya ulaşmadı, bu yüzden her sürümde periyodik kontrole alındı; kontrol bir UV tag'i için 50 ms'den pahalıysa o tag için atlanır, sayaçlar ve topoloji tetikleyicileri devrede kalır). Bunlar `TRACK_SCAN_INTERVAL` (1 sn) aralıkla karşılaştırılır, aralık taramanın kendi süresinin 10 katına kadar uzar (ana thread'in en fazla ~%10'u).
- **Yavaş okuma koruması:** Bir atomun serileştirilmesi 30 ms'den uzun sürerse, süresinin 4 katı kadar beklenmeden tekrar okunmaz (dev modellerde nokta sürüklerken).
- **Yeni UVW tag'i boş (0 eleman) doğar**; sahibine `ResizeObject(aynı, aynı)` çağrısı tag'i boyutlandırır. Alıcı bunu kendisi yapar (`load_uvw`).
- **Kapsam dışı (v1):** n-gon bilgisi, vertex map ve vertex normal tag'leri (weight ve pose morph Aşama 8'de geldi), aktif nokta/poligon seçimi (kullanıcıya özel görünüm durumu), spline'ların ek tipleri.

## 7e. Görünürlük: kim nerede, kim neye tıkladı (Aşama 7)

Blender'daki Multi-User eklentisi gibi: diğer kullanıcıların **kamerası** (renkli kamera ikonu + isim etiketi) ve **seçtiği nesneler** (kullanıcının renginde kutu + isim etiketi) 3B görünümde çizilir.

- **Çizim nesnesi:** Python'dan sadece nesneler viewport'a çizim yapabilir (SceneHook sadece C++). Oturum boyunca belgede gizli (`NBIT_OHIDE`), kendi tipimizde (`PRESENCE_OBJECT`) bir nesne durur; `Draw()` yer paylaşımını çizer. Bu nesne **asla senkronize edilmez** (`nodes.object_supported`), oturum bitince silinir.
- **Kamera:** Her kullanıcının aktif viewport kamerası (konum + sağ/yukarı/ileri eksenleri, 12 sayı) `presence.cam` olarak en çok saniyede 5 kez paylaşılır. Alıcı tarafta şekil, sonluluk ve eksenler doğrulanır (`overlay.parse_camera`). İkon boyutu izleyiciye uzaklıkla orantılıdır, ekranda hep benzer boyutta görünür.
- **Seçim:** Başkasının seçtiği nesne = kilidi. Kutu, nesnenin yönlü sınır kutusudur (`GetMp`/`GetRad`/`GetMg`); boyutsuz nesnelere (null) en az 15 cm'lik kutu çizilir. Kilitli tag, sahibi olan nesne olarak çizilir.
- **İsim etiketi:** Kullanıcının renginde çerçeve + `DrawHUDText` metni. Çerçeve rengi, metin rengi sabit olsa bile kimin kim olduğunu gösterir. Görüş açısı arkasındaki etiketler atlanır.
- **Renk adları:** Sunucu her kullanıcıya bir renk ve ad verir (Red, Blue, Green...). Panelde `Ad [Renk]` olarak görünür: "mavi olan" diye konuşulabilir.
- **Saf geometri:** `overlay.py` c4d import etmez; testleri C4D'siz çalışır.
- **Sınır:** Piksellerin ekranda görünmesi ancak C4D penceresinde elle doğrulanır (CHECKLIST 7.x). Ama çizim kodu gerçek C4D çizim API'sine karşı `tests/c4d_real/draw_check.py` ile çalıştırılır (c4dpy'de gerçek, ekran dışı bir `BaseDraw` var): tüm çağrılar kabul edilir, kamera ve nesne kutusu doğru okunur, demo kaplama 22 çizgi + 2 isim etiketi çizer. Komut: `py -c "..."` yerine `c4dpy tests/c4d_real/draw_check.py` (sonuç `C4D_COLLAB_DRAW_OUT` dosyasına yazılır).
- **Kayıt için iki dosya şart:** Nesne tipinin kaydı `res/c4d_symbols.h` ve `res/strings_us/c4d_strings.str` dosyalarını ister (içleri boş sayılabilir); yoksa "Could not initialize global resource" hatası verir. `GeResource.Init(<mutlak yol>)` ile doğrulandı: dosyalar yokken `False`, varken `True`.

### Canlı pencerede bulunan kayıt hatası (Aşama 7)
`RegisterObjectPlugin`, çağrıyı yapan **modülün** global `__res__` değişkenini arar; bu değişken sadece `.pyp` dosyasında vardır. Kayıt `presence.py` içinden yapılınca üç kayıt biçiminin hepsi `ReferenceError: Could not find required '__res__'` ile düştü ve çizim nesnesi hiç oluşmadı (kilitler ve eşitleme normal çalıştı). Çözüm: kayıt çağrısı `.pyp` içindeki `_register_object` fonksiyonundan yapılır; `__res__` yoksa `GeResource` ile kurulur. Panel ve mesaj plugin'leri bu değişkene ihtiyaç duymadığı için sorun yoktu. Kayıt sonuçları `c4d_collab.log` dosyasına yazılır.

### Canlı denemede bulunan iki sorun ve çözümleri (Aşama 7)

**1. R19'da nesnelerin içi görünüyordu** (küpün ön yüzü yok, silindirde tırtıklar). Kaplama nesnesi `DRAWPASS_OBJECT` geçişinde, sahnedeki nesnelerden **önce** çiziyor ve ekran matrisini/çizgi durumunu değiştirip bırakıyordu. 2023 her nesne için durumu sıfırlıyor, R19'un eski çizim hattı sıfırlamıyor; sonradan çizilen küp ve silindir bozuk durumla çizildi. Çözüm (canlı denemeyle düzeltildi): önce `DRAWPASS_HIGHLIGHTS` denendi ama C4D bu geçişi kendi vurgu renginde (beyaz, `GetHighlightPassColor`) çizer ve R19 hiç göstermez; bu yüzden renkler kayboldu. Asıl neden 2B çizimin (isim etiketleri) derinlik testini kapatıp bırakmasıdır. Şimdi `DRAWPASS_OBJECT`: kaplama nesnesi belgenin **en sonunda** durur (`presence.move_last`, `SceneSync.tick` kontrol eder), böylece sahne nesnelerinden sonra çizilir; ayrıca `Draw()` bitince `USE_Z/SETZ/OGL_CULLING` parametrelerini, `SetDepth(True)` ve nesne matrisini geri verir. Çizgiler kullanıcının renginde. Çizim nesnesi ayrıca eklenirken seçili kalıyordu, artık bırakılmıyor.

**2. Kamera ve nesne hareketi kasıyordu.**
- *Gönderim:* Kamera artık saniyede 20 kez (eskiden 5) gidiyor, 0,5 cm / 0,002 ölü bölgeyle (`sync.CAMERA_INTERVAL`).
- *Alıcıda yumuşatma:* Gelen kamera duruşuna gösterilen duruş zamana bağlı üstel olarak yaklaşır (`overlay.CameraSmoother`, τ = 70 ms, bir çağrı sayısına değil geçen gerçek süreye bağlı; takılan arayüzde kamera uçmasın diye adım 250 ms ile sınırlı). İlk görülen kamera yerinde belirir.
- *Nesne hareketi:* Başkasının sürüklediği nesnenin konum/dönüş/ölçeği artık anında yazılmaz, iki güncelleme arası süre kadar (30–120 ms) `smoothstep` ile kayar (`tween.TransformTweens`). Her adımdan sonra tracker'a "bunu ben yazdım" denir (yankı yok); dönüş kısa yoldan döner; yalnızca düz transform güncellemeleri kayar, diğerleri anında uygulanır. Silme/taşıma/geri yükleme kaymayı iptal eder.
- *Hafif yeniden çizim:* Sadece kamera/seçim/kullanıcı listesi değişince `DrawViews` (görünüm yeniden çizimi); sahne değişince `EventAdd` (yeniden değerlendirme). Eskiden her mesajda `EventAdd` vardı.
- *Zamanlayıcı:* Panel açıkken 33 ms (30 Hz) ağ, tarama ve animasyonu sürer.
- *Panel:* `local_addresses()` her yenilemede DNS sorgusu yapıyordu (bazı sistemlerde yüzlerce ms ana iş parçacığını kilitler); 60 sn önbellek. Panel metinleri sadece değiştiğinde yazılır.

### Host olunca R19'da kasma (gerçek rig: 1540 öğe, 106 XPresso, 51 Pose Morph, 335 adsız etiket)

Oturum açılınca iki zamanlayıcı (panelinki 16 ms, Cinema 4D'nin kendi mesaj zamanlayıcısı 20 ms) aynı işi iki kez yapıyordu ve her tarama bütün sahneyi geziyordu; yüzlerce ifadesi olan rig her değerlendirmede belge sayacını oynattığı için tam taramalar sürekli çalışıyordu. Düzeltmeler: (1) `bridge.timer_pump` iki zamanlayıcıyı 12 ms'den sık çalıştırmaz, panel 20 ms; (2) `EventAdd` (sahne yeniden değerlendirme: rig ifadeleri dahil) en çok 25/sn, geciktirilen istek kaybolmaz (`_EVAL_PENDING`); (3) tam taramalar işlemcinin yaklaşık %10'unu aşmaz (`FULL_SCAN_DUTY`); (4) etiket sayımı yalnızca paylaşma taramasında yapılır; (5) yalnızca dönüşümü değişen nesne için açıklamanın tamamı değil, önbelleğe alınmış üç parametre okunur (`params._dump_ids`); (6) seçim çerçevesi 400 poligon / 600 kenardan büyük mesh için kutuya düşer (R19 günlüğünde 850-1690 çizgi görüldü); (7) tanı: 30 ms'den uzun tam taramalar ve 40 ms'den uzun pump turları günlüğe (`Full scan took`, `Slow pump`) 10 sn'de bir yazılır.
Pose Morph etiketleri (yüz) artık senkronize ediliyor, bkz. "Yüz: Pose Morph".

### Karakter rigi ve büyük sahnede akıcılık (canlı geri bildirim: 1511 öğelik karakter sahnesi)

- **Cilt ağırlıkları:** Weight tag artık taşınır (`serializers/rig.py`, veri bölümü `"cw"`): eklem listesi (uuid), eklem başına dinlenme durumu (4 matris + uzunluk) ve nokta başına ağırlıklar (0..65535'e nicemlenmiş, C4D'nin kendi duyarlığı). Eklemler uuid ile adlanır; alıcı kendi eklem nesnelerini bağlar. Eklemler etiketten daha derinde olabilir (aynı toplu işlemde sonradan oluşur), bu yüzden `Applier._reload_weights` toplu işlem bitince ağırlıkları yeniden bağlar. Etiket, henüz taranmamış bir eklemin kimliğini isterse kimlik hemen verilir (`Registry.ensure`).
- **Ölçülen tuzak:** `GetWeightMap` seyrek haritalarda kısa ve yanlış liste döndürür (2023'te `[0,0,0,0,1]` → `[0]`); ağırlıklar `GetWeight(eklem, nokta)` ile nokta nokta okunur.
- **Boş poligon nesnesi:** `SetAllPoints([])` "Invalid object length" fırlatıyordu (gerçek rigde üç nesne bu yüzden yüklenmedi); boş mesh için çağrılmaz.
- **Neyin yerel kaldığı artık raporlanır:** oturum açılırken `Tracker.unsynced_report()` kod taşıyan nesneleri (altındaki çocuklarla), kod taşıyan ve desteklenmeyen etiketleri sayar ("Not shared (stays local): 3 x XPresso (tag) ..."), panel günlüğüne ve dosyaya yazılır. XPresso/Python ile sürülen kontroller karşıda çalışmaz, ama sonuçta değişen dönüşümler yine eşitlenir.
- **Tarama maliyeti (ölçüldü: 1500 nesne + 120 malzeme, 2023):** tam tarama 8-12 ms, 60 Hz'lik zamanlayıcıyla ana iş parçacığını doyuruyordu (kasma). Düzeltmeler: (1) seçili nesneler sürüklenirken yalnızca seçili nesnelere bakılır (`Tracker._scan_selected`, ~0-1 ms); gerisini en geç 0,5 sn'de bir tam tarama bulur, hızlı bakış bir şey bulmazsa hemen tam tarama yapılır, tam tarama süresi işlemcinin yaklaşık üçte birinden fazlasını almaz; (2) R19'da "animasyon" sayacı her değişiklikte arttığı için her taramaya iz taraması ekliyordu, artık iz karşılaştırması yalnızca aralıkla yapılır; (3) malzeme/shader gezintisi yalnızca MATERIAL/SHADER sayacı oynayınca (R20+); (4) `bridge.tick` bir taramanın maliyetinin iki katı kadar dinlenir (en çok 0,25 sn); (5) seçim çerçevesi için 2000 poligon / 3000 kenardan büyük mesh kutuya düşer, dünya kenarları nesne durduğu sürece önbellekten gelir.

### Rig misafirde bozuluyordu: bağlantılar, Include/Exclude listeleri, Mesh deformer (canlı geri bildirim)

Gerçek `Fight_test11.c4d` (744 nesne, 28 ağırlık etiketi) c4dpy'de tarayıcı → sunucu anlık görüntüsü → uygulayıcı yolundan geçirilip özgün dosyayla karşılaştırıldı (aynı karede hesaplatılarak). Eklem sırası, rest state ve ağırlıklar birebir aynı çıktı; bozukluk şuralardan geliyordu:

- **İleri başvuru (bağlantı):** anlık görüntü derinlik sırasıyla kurulur; bacak eklemindeki IK etiketi, kendinden daha derindeki `r_end` ekleminin bağlantısını yazdığında hedef henüz yoktu ve bağlantı sessizce atlanıyordu (IK zincirinin ucu yok → bacaklar 20-42 cm sapıyordu). `Applier._relink_forward`: toplu işlemden sonra, nesne/etiketlerin hedefi sonradan oluşan bağlantılarını (ve listelerini) yeniden yazar (`params.relink`). Gerçek C4D kontrolü: `c4dpy tests/c4d_real/links_check.py`.
- **Include/Exclude listeleri** (`CUSTOMDATATYPE_INEXCLUDE_LIST`: IK "Objects", Mesh deformer "Cages", Skin listesi...) artık taşınır: `{"I": [[uuid, bayraklar], ...]}`; paylaşılmayan üyeler atlanır.
- **Mesh deformer** (tip 1024543, "Cages" listesinden hesapladığı gizli ~22 KB bağ verisi parametre değil): misafirde `Initialize` düğmesi, Cages listesi yerine oturduktan sonra (oluşturulunca ya da Cages değişince) yeniden basılır (`serializers/deformers.py`, `Applier._bind_deformers`). Ölçüldü (2023): aynı Cages → aynı deforme noktalar, hangi karede bağlandığından bağımsız. Bu olmadan kol/bacak/gövde "autoweighted" katmanları hiç deforme olmuyor, parçalar havada kalıyordu.
- **Bilinen sınır (ölçüldü, çözülmedi):** (1) ~~XPresso etiketleri karşıya geçmez~~ (çözüldü, aşağıdaki “XPresso grafikleri ve kullanıcı verisi” bölümü); (2) yüz mesh'leri (kaş, dil, ağız, diş, göz) ve iki hedef spline'ı kopyada hâlâ farklı deforme oluyor (eklem/ağırlık/rest state aynı, nedeni bulunamadı); (3) Normal etiketi (156 adet) taşınmaz.
- **Misafirin karesi (asıl neden, ölçüldü):** Misafir belgesi kare 0'da açılıyordu, host'un rig'i ise animasyonuyla kendi karesinde (gerçek dosyada 185) duruyordu. Animasyonlu denetçiler her tarafta kendi karelerinde değerlendirilir, animasyonsuz değerler host'un karesinden gelir; sonuç anlamsız bir karışım (aynı dosya, kare farklıyken 744 nesnenin 496'sı sapıyordu, en çok 15 m; aynı karede 231'i, en çok 4 cm ve bunlar yüz sürgüleri). Düzeltme: host `host=True` ile karesini duyurur, misafir ilk duyuruda host'un karesine geçer (`SceneSync._align_time`), sonrasında herkes kendi oynatma kafasını korur. Ölçüm: `c4dpy tests/c4d_real/rig_check.py --same-frame` (bayraksız çalıştırınca misafir kare 0'da kalır).
- **Misafir kendi rig değerlendirmesini "düzenleme" diye geri gönderiyordu (canlı: kilit uyarı seli, bel tutulunca iki pencerede farklı duruş; ölçüldü):** Katıldıktan hemen sonra, kimse dokunmadan, misafirin izleyicisi 74 güncelleme gönderiyordu (`c4dpy tests/c4d_real/rig_check.py --same-frame`, "ops the guest would send" satırı). Kökler: (1) **Constraint etiketi** hedefleri bağlanınca ofsetlerini kendisi yeniden hesaplar: host'unkinden 13 m uzak (1366 cm); artık yeni Constraint etiketleri bir kez değerlendirilir, sonra host'un değerleri yazılır (`Applier._settle_constraints`); (2) rig'in yazdığı dönüşümler küçük gürültüyle oturur (eklem 0,02, null 0,005 cm); sabit 1e-7 eşiği bunu "değişiklik" sayıyordu, iki taraf birbirinin gürültüsünü gönderip duruyor, kilitli eklem sürekli "geri alınıyordu"; artık konum 0,02 cm, dönüş 0,003 rad, eklemde 0,05 cm / 0,03 rad altı düzenleme sayılmaz (`tracker.NOISE`, `JOINT_NOISE`); (3) Weight etiketi ağırlıkları 16 bit saklar, C4D 1/65535 farklı geri verir: aynı deri sayılır (`rig.same_weights`); (4) Mesh deformer'ın `Initialize`'ı kafeslere kendi Display/Compositing etiketlerini ekler, misafir bunları host'un kafeslerine eklemeye çalışıyordu: yerel kalır (`tracker.skip`); (5) B-Spline türü spline tanjant tutmaz, "tan silindi" gönderilmez. Sonuç: katılımdan sonra giden güncelleme 74 → 1 (Vibrate etiketli kamera, 0,3 cm).
- **Deneyip vazgeçilen (yanlış):** misafirde IK/Constraint etiketlerini kapatıp host'un sonuçlarını olduğu gibi göstermek. Kapatınca eklem dönüşleri sıfırlanıyor (host `l_knee` 1,412 rad, misafir 0), çünkü IK sonucu REL değerlerine yazılıyor ve etiket kapanınca eklem başlangıca dönüyor; çözücüler misafirde çalışmalı.
- **Günlük paneli:** yalnızca kimin katıldığı/ayrıldığı ve oturumun açılıp kapanması; "kilitli, değişikliğin geri alındı" gibi tekrarlayanlar durum çubuğuna gider.

### Misafirde kollar sahne boyunca uzuyor, gövde yok: Skin bağlama matrisi ve morph numaraları (asıl neden)

Önceki ölçümler yalnızca nesne **matrislerini** karşılaştırıyordu (en büyük fark 0,77) ve bu yüzden asıl bozukluğu göremiyordu: matrisler aynıyken Skin’in çizdiği mesh 17 m uzaktaydı. Doğru ölçüm, her mesh’in çizildiği **noktaları** (deform cache, dünya uzayında) karşılaştırmak ve iki tarafı render alıp bakmaktır. c4dpy ile `Fight_test11.c4d`, 354 mesh:

| | en büyük nokta farkı | 1 cm’den fazla kayan mesh |
|---|---|---|
| eski kod (kullanıcının gördüğü: kollar ekran boyunca uzamış, gövde yok) | 1714 cm | 62 |
| Weight etiketinin bağlama matrisi taşınınca | 24 cm | 14 |
| morph numaraları da korununca | 24 cm (`R_Local_Goal`, bir kontrolün 8 noktalı şekli) | 2 |

- **Weight etiketinin bağlama matrisi (`GetGeomMg`):** mesh’in bağlandığı andaki matrisi. Taşınmıyordu; misafirde yeni etiket mesh’i *şu an durduğu yerde* bağlıyor (host: 37,5/112,5/0, misafir: -473/114/1615) ve Skin her noktayı bu fark kadar kaydırıyordu. Eklemler, ağırlıklar ve rest state birebir aynıydı; Skin kapatılınca noktalar da aynıydı. Artık `"cw"` içinde `"gm"` olarak gider; yazıldıktan sonra Skin’in yeniden hesaplaması için etikete ve mesh’e `MSG_UPDATE` + `SetDirty` gönderilir (`serializers/rig.py`).
- **Pose Morph numaraları:** bir morph’un kaydırıcısı `(4000, 1001 + 100 * numara)`’dır ve XPresso portları, animasyon izleri ve parametreler morph’u bu numarayla tanır, listedeki yeriyle değil. Listede yeri değişmiş bir morph numarasını korur (host’ta bacak: `foot side` 2, `bend` 3, `foot front` 1; "AddMorph en düşük boş numarayı alır" yalnızca 2023'te doğru, R19 hep bir sonrakini verir, aşağıdaki R19 bölümüne bak); misafir morph’ları liste sırasıyla kurunca numaralar kayıyor ve sürücüler yanlış şekli oynatıyordu (`bend` `foot front`’un değerini alıyordu). `AddMorph` en küçük boş numarayı verir (2023’te ölçüldü): misafir önce her numarayı bir yer tutucuyla doldurur, her morph eklenmeden hemen önce kendi numarasını boşaltır, artan yer tutucular silinir (`serializers/morph.py`, `"i"`).
- **Doğrulama:** `tests/c4d_real/live_rig_check.py` artık mesh noktalarını da karşılaştırır. Eski kodla misafir render’ı kullanıcının ekran görüntüsündeki bozukluğu birebir verir, yeni kodla host’unkiyle aynıdır.

### Misafirde kemik halkaları görünüyor, gövde parçaları birleşmiyor: katmanlar ve IK-Spline’ın gizli verisi

Önceki düzeltmeden sonra misafir yalnızca host’un karesinde (185) doğruydu. Başka bir karede host ile misafiri aynı kareye getirip karşılaştırınca (`frames_probe`: iki belgeyi 0, 40, 100, 185, 260’a alıp tüm mesh noktaları ve nesne konumları) 200 mesh 2-3 m sapıyordu; bel/kalça birleşim yerleri bu yüzden kopuyordu. İki neden:

- **Katmanlar senkronize edilmiyordu.** Host’ta 7 katman var (`JOINTS` “View” kapalı: 108 eklem gizli; `MESH`, `CONTROLS`, ...), misafirde hiç yoktu. Sonuçlar: (1) eklemlerin yuvarlak kemik çizimleri misafirde görünüyordu; (2) katmanın Animasyon/İfade/Deformer/Üreteç bayrakları değerlendirmeyi de değiştirir. Artık katmanlar belge verisinde (`"ly"`, `serializers/doclayers.py`: ad, ebeveyn, bayraklar, renk) gider ve her nesne/etiket katmanını `ID_LAYER_LINK` bağlantısıyla (uuid) söyler (`params.py` bu parametreyi artık dışlamıyor). Katmanlar eklenir/güncellenir, silinmez.
- **IK-Spline etiketinin gizli verisi taşınmıyordu.** Bel spline’ının noktalarını (3 nokta, kontrol nesnelerine bağlı) IK-Spline etiketi her değerlendirmede yazar (etiketi kapatınca nokta sabit kalıyor). Etiket bunun için açıklamada (description) adı geçmeyen kendi konteyner anahtarlarını kullanır (`10000 = 3`, ...). Bunlar yoktu, misafirdeki etiket spline’ı hiç oynatmıyordu: spline host’un 185. karedeki noktalarında takılı kalıyor, omurga/kalça/bacaklar her karede yanlış duruyordu. Bu değerleri kopyalayınca misafirin spline’ı host gibi hareket etti. Artık `"hd"` bölümü (`serializers/hiddendata.py`) IK, IK-Spline ve Constraint etiketlerinin bu tamsayı/ondalık/vektör/matris değerlerini taşır.
- **Düzenleme sayılmayanlar:** IK-Spline’ın hareket ettirdiği spline’ın noktaları (çıktısıdır), Constraint/IK etiketlerinin gizli sayaçları (her değerlendirmede değişir) misafirden host’a gönderilmez (`Tracker._mask`).
- **Sonuç (c4dpy, 2023):** host ile misafir aynı karede 0, 40, 100, 185, 260’ta: en büyük mesh noktası farkı 301 cm → 24 cm (tek mesh, aşağıya bakın), 1 cm’den fazla kayan mesh 200 → 2 (frame 100’de 41). Gerçek ağ oturumunda (`live_rig_check.py`): host rig’i değişmez, misafirin gönderdiği sahte güncelleme 5.
- **XPresso portlarının DescID’si (bu turda iyileşti):** (1) Constraint etiketinin hedef ağırlıkları XPresso’da hedef nesnenin adıyla görünür (“world”, “local”); description bu adı bilmez, parametre `8502 + hedef sırası`dır, port numarası sırasıyla eşlenir (`_constraint_parameters`). (2) Aynı adlı kullanıcı verisi öğeleri (sol/sağ el için `Tool`, `Texture`, `Size`) port numarası sırasıyla eşlenir (`_same_names`). (3) Condition/Math düğümlerinin veri tipi bağlandıkları şeyden gelir (`SetPortType` onları ayarlayamaz): bağlantılar başarısız olanlar yeniden denenerek kurulur. Sonuç: host ile misafirin grafik farkı olan etiket sayısı 10 → 4; eksik 24 bağlantıdan yalnızca aletlerin doku seçimi (10 Condition → Bitmap dosya portu) kaldı.
- **Bilinen küçük fark:** `R_Local_Goal` (8 noktalı kontrol kutusu) host’ta kutu, misafirde bir noktaya çökmüş görünür: host’taki Pose Morph etiketi dosya yüklendikten sonra yeniden hesaplanmamış, misafirinki “off” şeklini (güç 1,0) uyguluyor. Host etiketi yeniden hesaplarsa aynısı olur.

### Uzuv uzatılınca misafirde bozuluyordu, kafa tutma (World Head) portları yanlış parametreye bağlanıyordu

Önceki ölçümler rigi yalnızca olduğu gibi (animasyonun kendi karelerinde) karşılaştırıyordu; bu turda host'ta kontroller **sürüklenip** değişiklik misafire canlı oturum yolundan (izleyici → sunucu → uygulayıcı) gönderildi ve ikisi karşılaştırıldı (`c4dpy tests/c4d_real/limb_check.py`, 2023, `Fight_test11.c4d`).

- **Uzuv uzatma (asıl neden, ölçüldü):** sol bacak hedefi (`L_Foot_Goal`) bacağın erişimini aşacak kadar uzağa çekilince IK etiketinin Squash/Stretch'i bacağı uzatır; uzatma eklemin **ölçeğine** (host'ta uyluk Y ölçeği 1,5) yazılır. Host bu ölçeği (ve eklemlerin konum/dönüş sonuçlarını) düzenleme diye gönderiyordu; misafir onları yazıp **kendi IK'sini de çalıştırdığı için** uzatma, o anki ölçek üzerinden bir kez daha yapıldı: hedef uzaklaştıkça misafirde 2,0, 2,5... (host'ta 1,5'te kalıyor), diz/topuk 18-130 cm kayıyor, bacak mesh'i ekran görüntüsündeki gibi uzuyordu. Düzeltme: oturumda kullanılan bir IK ya da IK-Spline etiketinin zincirindeki eklemlerin (etiketin nesnesinden uç eklemine kadar) konum, dönüş, ölçek ve eklem uzunluğu değişiklikleri düzenleme sayılmaz (`Tracker.solved_joints`, `_mask`; etiket kapalıysa, "Use IK" kapalıysa ya da IK/FK = FK ise zincir çözülmüş sayılmaz, eller eklemleri kullanabilir). Zincir canlı etiketlerden okunur: XPresso'nun sürdüğü anahtar shadow'da eski değerde kalır.
- **Uzamış bacakla katılma:** katılan taraf eklemleri host'un uzamış ölçeğiyle (1,5) alıyor ve kendi IK'siyle 2,0'a uzatıyordu; yeni oluşan çözülmüş eklemler ölçek 1'den başlar, misafirin IK'si host'a ulaştırır (`Applier._reset_solved_scale`). Not: bir eklem zincirinin dinlenme ölçeği 1 değilse (yansıtılmış eklem) bu varsayım yanlıştır; bu rigde bütün eklemler 1.
- **Okuma tuzağı:** etiket parametresi `tag[2002]` ile okunursa etiketin konteynerine bakılır, misafirde yeni yapılmış etikette orada anahtar yoktur (`None`); açıklama kimliğiyle (`GetParameter(DescID)`) okunur.
- **Kafa tutma (World Head) ve bacak karışımı portları (ölçüldü):** rigde kafa eklemindeki Constraint etiketi (Transform, iki hedef: `[dont touch] head_world`, `head_world2`) ve bacaklardaki Constraint etiketleri XPresso'dan sürülüyor. Grafikteki Object düğümünün portları "world"/"local"/"Strength" diye adlanır; description bu adları bilmez, ad aranırken ilk eşleşme alınıyordu: ağırlıklar `8502/8503` (Parent grubunun "Weight" şablonu), güç `2203` (Parent grubunun Strength'i). Gerçek parametreler Transform modunda **10002/10012** (hedef n: `10001 + 10 n` bağlantı, `+1` ağırlık) ve **2116**'dır (host'ta düğüm portları bunlara bağlı, adı hedef nesnenin adı). Misafirdeki grafik böylece Transform modunda hiçbir işi olmayan parametreleri sürüyordu; gerçek ağırlıkları yalnızca host'tan gelen değerler belirliyordu (bu rigde değerler aynı çıktı, ama misafir kendi hesabını yapamıyordu). Artık Constraint etiketi için açık olan modların Strength'i ve hedef ağırlıkları description'dan okunur (`xpresso._constraint_parameters`); port numaralarının sırasıyla hedeflere eşlenir. Düzeltmeden sonra host ve misafirde World Head 0/1 değişince ağırlıklar ve kafa aynı (0,00 cm).
- **Asıl kafa tutma nedeni: R19 misafirinin logu (canlı oturumdan okundu):** misafir R19 idi (host 2023) ve `c4d_collab.log`'unda 1054 kez `Cannot build the ports of XPresso node` vardı: R19'da `Description.GetParameter(id)` ikinci argümanı (`ar`, AtomArray) ister, `TypeError` fırlatır; Object düğümü portlarının adlarını parametre kimliğine çeviren `_name_table` bu yüzden hiçbir vektör parametresi ("Position . X") için çalışmıyordu ve bu düğümlerin portları kurulmuyordu (kafa/el eklentilerinin dünya dönüşümünü kopyalayan, kafa karışımını süren grafikler yarım kalıyordu). `_level_name` önce tek, `TypeError` olursa iki argümanla çağırır (`tests/adapter/test_solvers.py::Release19Test`). 2023'te bu hata yoktu, bu yüzden c4dpy ölçümleri kafayı gösteremedi. R19'un hatası 2023'te taklit edilince (`tests/c4d_real/r19_sim.py r19`: `_level_name` hep `TypeError` verir) belirti yeniden üretildi: 241 nesne (kafa, kafa ve el eklentileri, dil) host'tan 200-284 cm uzakta, 68 mesh yanlış yerde; normal yolda 0. **R19'da bu düzeltme gerçek R19 ile henüz denenmedi** (c4dpy yok); denemek için R19'da oturuma katılıp logda `Cannot build the ports` kalmadığına bakmak yeter.
- **Her taramada reddedilen taşıma (aynı log, 439 000 satır):** Mesh deformer'ın `Initialize`'ı kafeslerin başına yalnızca bu bilgisayarda olan Display/Compositing etiketleri ekler (`tracker.skip`); tarama, sonraki paylaşılan etiketin "önceki kardeşi" olarak bu yerel etiketi sayıyordu, oturumun bunu kardeş bilmediği için her taramada 30 `move` üretiliyor ve `prev is not a sibling` diye reddediliyordu (c4dpy'de ölçüldü: 30 → 0). Günlük şişiyor ve her tarama boşuna işleniyordu (R19 günlüğünde 200 ms'lik "Slow pump"lar). Yerel etiket/nesne artık `prev` olarak sayılmaz (`Tracker._visit_tags`, tarama döngüsü; `SkippedTagTest`).
- **Kare değiştirmek tek başına "düzenleme" gönderiyordu (canlı geri bildirim: guest 170'te, host 185'te; kırmızı/mavi/yeşil kontroller vücuttan kaymış):** iki taraf kendi oynatma kafasında olduğu için rig'in o karedeki çıktısı karşıya "düzenleme" diye gidiyordu: Constraint etiketli nesneler (kafa, dirsekler, el aletleri) ve XPresso'nun **Global Matrix** portuyla sürülen nesneler (kafa ve el eklentileri; port bir parametre portu olmadığından `"dr"` listesine hiç girmiyordu). Ölçüm (c4dpy, sadece zaman kaydırılırken): taraf başına kare adımında 16-19 güncelleme (Head Attachments, Left/Right Hand Attachments, head, l_elbow, r_elbow, left/right_tools); düzeltmeden sonra 0. Karşı taraf bunları kendi karesinde yeniden hesapladığı için 2023'te son durum zaten aynıydı; ama bir sürümde rig tam değerlendirilemiyorsa (R19'da yukarıdaki port hatası) bu değerler göndericinin karesinde asılı kalıyordu: yalnızca host'un karesinde doğru görünme, başka karede kayma (`r19_sim.py r19`: kare 185'te 0, 0/60/100'de 241 nesne). Düzeltme: (1) Global Matrix portu bağlı bir nesnenin konum/dönüş/ölçeği `"dr"` içine girer (`xpresso.TRANSFORM_KEYS`); (2) açık bir Constraint etiketinin taşıdığı nesnenin dönüşümü düzenleme sayılmaz (`Tracker.constrained_objects`). Etiket kapalıysa (ya da Constraint'siz) elle düzenleme eskisi gibi gider. Kontrol: `c4dpy tests/c4d_real/frames_check.py` (host 178'de kalır, guest 190'a oynar; iki taraf da kendi karesini gösterir, kare kaydırmak güncelleme göndermez).
- **Seçince misafirin rigi bozuluyordu, R19 host / 2023 misafir (canlı geri bildirim: Auges kontrolleri vücuttan ayrı, parçalar düz renkli, "doku gitti" sanıldı):** başka sürüm, aldığı bazı değerleri farklı saklar (UVW, Texture ve Phong etiketleri, eklem dönüşümleri); okununca göndericinin verisinden farklı çıkarlar, kimse dokunmadan. Bir nesneyi seçmek sayaçlarını oynatıyordu: fark ya "düzenleme" diye host'a gidiyordu (host logu: 267 işlem, `joints`, `UVW`, `Texture`, `Phong`) ya da nesne host'ta kilitliyse her taramada gölgeden "geri alınıyordu" ve misafirin rigi bozuluyordu. 2023 host / R19 misafir ile görülmemişti (o yönde misafir R19'du). Düzeltme: uzak veri yazılınca atomun bu taraftaki okuması saklanır (`Tracker._capture_applied`); bir fark yalnızca o okumadan da farklıysa düzenleme ya da geri alma sayılır (`_differs_where`, parametre parametre). Gerçek bir düzenleme saklananı siler. Kontrol: `c4dpy tests/c4d_real/version_echo_check.py` (Phong açısı 180'in üstünde gönderilir, misafir 180 okur; eski kodla 40 gönderim ve 336 geri alma, yeni kodla 0 ve 0; gerçek düzenleme yine gider).
- **Kafesler misafirde düz renkli bloklar olarak çiziliyordu, hareket sırasında dokulu gövdenin üstünde (canlı: R19 host, 2023 misafir; `dump_skin.py` iki tarafta: Skin, Mesh deformer, katman ve görünürlük, noktaların yeri aynı, 4 cm içinde):** 2023'ün Mesh deformer'ı `Initialize` basılınca kafeslere bir **Display** etiketi (kendi gölgeleme modu) ve bir **Compositing** etiketi ekliyor; host'un kafeslerinde ikisi de yok. Misafir onları yerel sayıp tutuyordu, kafesler başka gölgelemeyle çiziliyordu. R19'un Initialize'ı bunları eklemiyor, o yüzden 2023 host / R19 misafir düzeninde hiç görülmedi. Düzeltme: `Applier._bind_deformers` bu iki türü (`deformers.INITIALIZE_TAGS`) Initialize'dan sonra siler; kafesler host'taki gibi görünür. Bağ verisi deformer'da kalır (limb_check: meshler aynı).
- **Doğrulama:** `c4dpy tests/c4d_real/limb_check.py` (sürükleme: bacak, kollar, uzamış bacakla katılma, World Head, Constraint portları). Eski kodla: bacak 133 cm, katılma 37,5 cm, uyluk ölçeği 2,0 / 1,5 (4 başarısız); yeni kodla hepsi geçer (en büyük fark 0,30 cm, kılıç mesh'i). `tests/adapter/test_solvers.py`: zincir bulma, maske, port eşlemesi (sahte c4d). Gerçek iki süreçli oturum (`live_rig_check.py`) değişmedi: 7/7.
- **Bilinen sınır:** (1) zincirdeki eklemlere elle yapılan düzenleme (dinlenme konumunu değiştirmek gibi) IK açıkken karşıya gitmez; IK/FK = FK ya da etiket kapalıyken gider. (2) Dil ve ayak topuk hedefleri (`Joint.2.Goal`) 60 cm gibi uç sıçramalarla birkaç cm farklı çözülür; adım adım sürüklenince aynı. (3) Dilin IK-Spline kontrolleri hâlâ farklı sonuç verebilir (yüz, ölçülmedi).

### XPresso grafikleri ve kullanıcı verisi: misafirde rig’in kuralları

Katılan taraftaki bozuk rig’in asıl nedeni, rig’i süren kuralların (78 XPresso etiketi) ve kontrol panelinin (nesne **kullanıcı verisi**: Skin, Scale, Tool, Texture, Controls...) karşıya hiç geçmemesiydi: misafirde kontrol sürgüleri sınırlanmıyor, ölçek/görünürlük/yüz şekilleri sürülmüyor, XPresso’nun okuyacağı kullanıcı verisi alanları yoktu. Ölçüm (`c4dpy tests/c4d_real/rig_check.py --same-frame`, 744 nesne): aynı karede en büyük konum farkı 4,07 → **0,77**; farklı karede (eski hâl) 15 m’ye çıkıyordu.

- **XPresso grafiği veri olarak taşınır** (`serializers/xpresso.py`, etiket verisinde `"xp"`): düğümler, ayarları, portlar, bağlantılar. **Güvenlik:** yalnızca standart operatörler (400001000-400001161, COFFEE hariç) taşınır; Python/COFFEE/üçüncü taraf düğüm içeren grafik eskisi gibi yerel kalır ve ağdan gelen veri bu düğümleri hiçbir zaman oluşturmaz (`load` reddeder).
- **Object düğümü portları:** port yalnızca görünen adı söyler (“Position . Y”); parametrenin DescID’si, bağlı nesnenin description’ında o ad aranarak bulunur. Ad benzersiz değildir (“Scale” hem nesne ölçeği hem kullanıcı verisi kaydırıcısı), bu yüzden port veri türü (Real/Vektör/Tamsayı...) ayırt eder. Port numaraları (`1000000n` giriş, `2000000n` çıkış, ikisi için tek sayaç) düğümün parametreyi **ilk kullanım sırasıdır** ve silinen port numarasını boş bırakır; düğümün ayar konteyneri bu numaralarla tutulduğundan misafirde aynı numaralar yedek parametrelerle üretilip yedek portlar silinir (yoksa her değerlendirmede `CRITICAL: Stop [ge_container.h]`).
- **Kullanıcı verisi** (`serializers/userdata.py`, `"ud"`): eleman tanımları `SetUserDataContainer` ile **aynı id’lerle** oluşturulur; elemanın konteynerinin kendi id’si veri türüdür (`BaseContainer(dtype)`), başka id’yle kopya başka bir elemandır (DescID türü -1 çıkar). Konteyner içeriği `serializers/containers.py` ile JSON’a çevrilir (Vector, Matrix, bağlantı, iç içe konteyner, SplineData, Filename).
- **Filename parametreleri:** `SetParameter` kullanıcı verisindeki dosya adını düz metin (130) olarak yazıyordu; XPresso o alanı her okuyuşta `CRITICAL: Stop` veriyordu. `params.load` dosya adı alanlarını öğe ataması (`atom[desc] = yol`) ile yazar (Filename, 131).
- **Protection etiketi:** misafirde etiket eklenip sınırları yazılınca nesne sınırına (0) çekiliyordu (host’ta -2,05 olan kaydırıcı misafirde 0; HEAD’de de vardı). Tüm etiketler yerleştikten sonra dönüşümler host’un gönderdiğine göre yeniden yazılır (`Applier._restore_transforms`).
- **Sürülen parametreler düzenleme değildir** (`"dr"`: bir grafiğin bağlı giriş portlarının yazdığı parametreler): iki taraf da aynı grafiği değerlendirir, sonuç düzenleme sayılmaz. `Tracker._mask_driven` bu değerleri (vektör bileşeni düzeyinde) ve Pose Morph kaydırıcılarını oturumdaki değere çevirir. Katıldıktan sonra misafirin gönderdiği sahte güncelleme: 74 → 8 (kalanlar: kamera ve IK/Constraint gürültüsü).
- **Gerçek oturumda doğrulama (2023, iki c4dpy süreci, gerçek ağ):** `c4dpy tests/c4d_real/live_rig_check.py` host `Fight_test11.c4d`’yi açar, guest ağdan katılır. Sonuç: guest’te 744/744 nesne eşleşir, en büyük fark 0,75 (aynı kare 185), günlükte hata yok; **host’un rig’i guest katıldıktan sonra değişmez (en büyük hareket 0,0000)**; guest’in kendiliğinden gönderdiği güncelleme 3. İki ek düzeltme bunun için gerekti: (a) **Vibrate etiketi** (kamera sarsıntısı) her değerlendirmede farklı sonuç verir, guest bunu “düzenleme” diye gönderiyor ve host kamerasını 0,3-0,6 oynatıyordu; sarsıntı büyüklüğündeki dönüşüm değişiklikleri düzenleme sayılmaz (`tracker._mask_shake`); (b) guest’teki Pose Morph etiketinde XPresso portu yüzünden host’ta olmayan parametre anahtarları görünüyordu; Pose Morph etiketinde yeni parametre anahtarı düzenleme sayılmaz (morph’lar `"pm"` ile gider).
- **Bilinen sınır:** (1) Constraint etiketinin “world”/“local” portları: bu tur çözüldü (yukarıdaki "Kafa tutma" maddesi), bu cümle eskidir; (2) bir Layer’a bağlı Object düğümünün portları kurulmaz (katmanlar eşitlenmez); (3) 10 grafik host ile birebir aynı değil (bu iki neden). Doğrulama: `c4dpy tests/c4d_real/xpresso_check.py` (host grafiği ile misafirde kurulan grafik aynı mı). **R19’da bu sürüm gerçek Cinema 4D ile denenmedi** (c4dpy yalnızca 2023’te var; `SplineData.GetKnots`, `SetKnot` ve `AddPort` imzaları 2023’e göre yazıldı, R19 için geri dönüşler konuldu).

### R19 misafiri: ölçülen kısıtlar ve bunlara karşı yapılanlar

R19 misafirinde rig'in kafa, bacak, yüz ve doku tarafı 2023'ten farklı çalışıyor; hepsi gerçek R19'da ölçüldü (`tests/c4d_real/tools/`: `r19_ports_probe.py`, `dump_foot.py`, `dump_textures.py`; sonuçlar `%LOCALAPPDATA%\Temp\hp\`).

- **XPresso Object düğümünün Global Matrix portları (30000001 / 40000001) Python'dan kurulamıyor:** `AddPort` her kimlik, bayrak ve DescID biçiminde `None` döndürüyor (`AddPortIsOK` doğru dese de); parametre portları (DescID ile) çalışıyor. Kafa/ek parça grafikleri ("kontrolün global matrisini nesneye kopyala") bu yüzden kurulamıyordu. Karşılık: kurulamayan Global→Global bağlantısı `xpresso._MIRRORS`'a yazılır ve **iki yoldan** uygulanır: (1) hedef nesneye eklenen yerel Python tag'i "Collab rig copy" (`xpresso._copy_tag`; ifade geçişinin içinde, rig'in kendi ifadelerinden sonra çalışır, oynatmada da aynı karede; kod sabit ve eklentinin içinde, ağdan hiçbir şey karışmaz, tag gönderilmez), (2) yedek olarak her pompa turunda `xpresso.apply_mirrors` (`bridge.mirror`). Oturum bitince/yeniden başlayınca kopyalar ve tag'ler silinir (`xpresso.clear_session`).
- **`AddPort` merdiveni (`xpresso._add_port`):** mesajsız, mesajlı, bağlı nesne parametresinden yeniden bağlayıp, DescID'nin yaratıcısız biçimleri; hangi basamağın hangi sürümde kazandığı kayıtlı değil, yalnız Global Matrix için hepsinin başarısız olduğu ölçüldü. Port reddi `AddPortIsOK` sonucuyla loglanır.
- **Pose Morph numaraları:** R19 `AddMorph`'ta numarayı yeniden kullanmaz (`[0, 3, 4]` ≠ gönderilen `[0, 1, 2]`), numaralar eşitlenemez. `morph.NUMBER_MAP` {gönderici numarası → buradaki} tutar; XPresso portları (`xpresso._slider`) ve animasyon izleri (`tracks.find_or_create_track`) buradaki numaraya çevrilir, iz kendi tarafından raporlanırken gönderici anahtarına geri çevrilir (`morph.sender_key`) ki iz aynı uuid'yi taşısın. Tag bu numaralarla zaten kuruluysa her güncellemede yeniden kurulmaz.
- **Eksik kurulan grafik geri gönderilmez:** kurulamayan bağlantısı olan ya da gönderenin grafiğinden farklı kurulan XPresso tag'i `xpresso.DEGRADED`'a girer, tracker onun `xp` verisini shadow'daki ile sabitler (`Tracker._mask`); aksi halde host kendi çalışan grafiğini misafirin eksik grafiğinden yeniden kurardı.
- **Doku:** C4D mutlak yolu bir süre sonra çıplak ada kısaltabiliyor; belgesi klasörsüz misafir dosyayı bulamıyor ve karakter siyah kalıyor. `assets.apply_files` yazdıktan sonra tutulan değeri okur, bulunamıyorsa önbellekteki kopyaya yönlendirir; `Applier.repair_textures` bunu her 2 sn'de tekrarlar. Kısaltılmış ad düzenleme sayılmaz (`assets.original_value`).
- **Yansıma:** R19, Reflection kanalı açılınca kendiliğinden varsayılan bir katman ekliyor; `layers.has_extra` parametrelerden sonra ve her 2 sn'de fazlasını siler.
- **Hız (R19'da tam tarama ~500 ms):** zaman çizelgesi oynarken ya da son 0,6 sn'de sürüklenmişken tam tarama atlanır, yalnızca seçili nesnelere bakılır (`SceneSync._animating`, `Tracker.scan(quick_only=True)`); oynarken eklentinin kendi `EventAdd` çağrısı yapılmaz (zaman çizelgesi zaten her kareyi değerlendiriyor); R19'da (ayrıntılı sayaç yok) hızlı bakış bir şey bulmazsa tam tarama sırasını bekler: büyük taramalar (150 ms üstü) maliyetin 19 katı, diğerleri 9 katı bekler. Log'a 30 sn'de bir `Pump stats` yazılır (yalnız pompa %10'dan fazla meşgulse ya da 10 dakikada bir).
- **Log:** `c4d_collab.log` 5 MB'ta döner (+2 eski dosya); Python 3'te UTF-8, Python 2'de (R19) baytlar olduğu gibi yazılır.

### Yüz: Pose Morph (Aşama 8)

Ölçümler: `tests/c4d_manual/probe_posemorph.py` (2023, c4dpy ile otomatik; R19 bekleniyor). Kod: `serializers/morph.py`.

- **Veri (tag bölümü `"pm"`):** `{"n": sahibin nokta sayısı, "m": [{"n": ad, "d": {parça: float x3}}...], "s": [güç | null]}`. Morph'lar tag'deki sırayla; 0 = base poz, nokta taşımaz. `"d"` base'e göre **göreli** farklar, 128 noktalık parçalar; tamamı sıfır olan parçalar gönderilmez (bir gülümseme binlerce noktanın birkaç yüzünü oynatır). Güç `null` = track ile sürülüyor, alıcı kendi değerini korur. Yalnızca nokta morph'ları taşınır; konum/dönüş/ölçek, UV, parametre, harita ve hiyerarşi morph'ları yerel kalır.
- **Okuma/yazma:** morph `SetMode(EXPAND, REL)` ile açılır, `GetFirst().GetPoint/SetPoint`, sonra `SetMode(COLLAPSE, AUTO)`. Kapalı (collapsed) morph anlamsız tek nokta döndürür. Güç slider'ları `GetMorphID(i)` = DescID `(4000, 1001 + 100·i)`; tag açıklamasında **yok**, genel parametre serileştiricisi görmez.
- **Ölçülen sayaç davranışı:** morph noktası düzenleme, yeniden adlandırma, morph ekleme ve aktif poz değiştirme **hiçbir sayacı oynatmıyor**; güç değişikliği tag `DATA`, `RemoveMorph` tag `DATA`. Bu yüzden periyodik kontrol var. Ama okuma pahalı (10.000 noktalı morph ~3 ms, yazma ~17 ms; gerçek rigde 51 tag), bu yüzden noktalar yalnızca tag'in **imzası** (mod, aktif poz, morph adları, nokta sayısı) değişince yeniden okunur; yoksa son okuma kullanılır (`Tracker.morph_cache`, her oturumun kendi önbelleği). Kullanıcı yüzü düzenleme modunda şekillendirip başka poza ya da animasyon moduna geçince imza değişir ve şekil gider. Pose morph kontrolü pahalı diye periyodik kontrolden düşürülmez (tek kanal o).
- **Tag sonucu mesh'in kendi noktalarına yazıyor (ölçüldü, deform cache yok):** animasyon modunda mesh = base + güç × fark; düzenleme modunda seçili poz mesh'te görünür. Eskiden her slider hareketi (XPresso her değerlendirmede) yüzün deforme hâlini **geometri olarak** gönderiyordu. Şimdi: mesh'in noktaları yalnızca düzenleme modunda base poz seçiliyken base kabul edilir (`morph.mesh_shows_base`). Diğer durumlarda paylaşılan geometri korunur, noktalar okunmaz bile; paylaşılan bir şey yoksa (yeni nesne, sayılar değişti) base, base pozdan mutlak modda okunur (`morph.base_points`).
- **Alıcı:** geometri gelince Pose Morph'lu mesh'e noktaların **tamamı** yazılır (diğer parçalar animasyonlu yüzü gösteriyor olabilir), sonra base poz yeniden `Store` edilir (`morph.store_base`, ölçüldü: tag yeni base'i kullanır). Morph listesi farklıysa tag baştan kurulur ve tüm parçalar yazılır (yeni morph `Store` anında mesh'te ne varsa onu alır); aynıysa yalnızca değişen parçalar yazılır. Alıcı tag'i hep animasyon modunda tutar.
- **Yerel kalan:** düzenleme/animasyon modu (`ID_CA_POSE_MODE`) her kullanıcının kendi görünümü, gönderilmez.
- **Kilit:** kilitli yüzde yalnızca güçler değiştiyse (rig değerlendirmesi) geri alınmaz; şekil veya ad değiştiyse geri alınır.
- **Nokta sayısı uyuşmazsa** noktalar yazılmaz, günlüğe uyarı düşer; gönderilen hâl hatırlanır, böylece alıcı kendi (yazılmamış) hâlini düzenleme diye geri göndermez.
- **Gerçek C4D senaryosu (2023↔2023):** şekiller, güç, animasyon modu, misafirde deformasyon, slider hareketinde geometri gitmemesi, paylaşılan mesh'in base olması ve değerlendirme sırasında yankı olmaması kontrol edilir.

### Malzemeler, shader'lar ve doku dosyaları (canlı geri bildirim: R19'da doku görünüyor, 2023'te gri)

- **Yeni düğüm türleri:** `material` (belge düzeyi, kardeş zinciri), `shader` (ebeveyn: malzeme ya da shader; iç içe shader'lar), `asset` (doku dosyası). `KIND_ORDER` = asset, material, shader, object, tag, track, doc: anlık görüntüde (BFS) önce dosyalar ve malzemeler gelir, çünkü doku etiketleri malzemeye bağlanır.
- **Veri:** nesne ve etiketlerle aynı genel parametre serileştiricisi (`params.dump`, açıklamaya göre). Bu yüzden Corona gibi eklenti malzemeleri de, o eklenti karşıda kuruluysa, taşınır; olmayan tür atlanır (malzeme için standart malzeme yer tutucu, doku etiketi bağlantısı bozulmasın diye).
- **Bağlantılar:** `MATERIAL_COLOR_SHADER` gibi shader bağlantıları malzeme oluşturulduğunda henüz yok olan shader'ı gösterir; `Applier._relink` bir toplu işlemde yeni shader alan malzemelerin bağlantı parametrelerini yeniden yazar. Tarama her turda önce tüm malzeme ve shader'lara kimlik verir (`_assign_shading_uids`), sonra nesneleri gezer; böylece doku etiketinin malzemeye bağlantısı ilk taramada çözülür.
- **Değişiklik algılama:** malzeme ve shader için ayrı ayrı `GetDirty`; belge sayacı olarak `HDIRTYFLAGS_MATERIAL` ve (varsa) `HDIRTYFLAGS_SHADER` (ölçüldü: shader düzenlemesi MATERIAL sayacını değil SHADER sayacını artırır; R19'da ALL).
- **Ölçülenler (2023):** shader çocukları yalnızca `GetFirstShader()/GetNext()` ile gezilir (`GetUp/GetDown` shader'larda boş döner); `c4d.Filename` yok, dosya adı düz metin. Malzemeyi yerel `.c4d` olarak dışa aktarmak (320 ms) ve yerel dosya yüklemek denendi ve reddedildi: yavaş ve karşı tarafta dosya ayrıştırmak güvenlik riski.
- **Doku dosyaları (`serializers/assets.py`):** göreli yol belgenin yanında, `tex` klasöründe ve genel doku klasörlerinde aranıp mutlak yola çevrilir. Dosya varsa (≤ 25 MB, yalnızca görüntü uzantıları) SHA-1'i bir `asset` düğümü olur (uuid = SHA-1, bir dosya bir kez gider; oturumda toplam 300 MB sınırı). Malzeme verisinde `"f": {parametre: {h: sha1, n: ad}}`. Alıcıda dosya kendi yolunda varsa o kullanılır, yoksa önbellek klasöründeki kopyaya yönlendirilir; yönlendirme yerel kalır (izleyici özgün yolu bildirir, yankı yok). Kabul kuralları: yalnızca resim uzantıları, dosya adı temizlenir ve önbellek klasöründen çıkamaz, açılmış boyut sınırlı, içerik SHA-1 ile eşleşmeli.
- **Yansıma (Reflectance) katmanları (`serializers/layers.py`, canlı geri bildirim: "doku soyutlaşıyor, ışık yansıyor"):** yeni açılan standart malzeme kendiliğinden **1 varsayılan yansıma katmanıyla** doğar; katmansız bir malzeme (sahnedeki 119 malzemenin 59'u böyleydi) karşıda fazladan bir specular parlaması kazanıyordu. Artık malzeme verisinde `"rl": [[bayraklar, ad], ...]` (katman sırasıyla, boş liste = katmansız) taşınır; alıcıda önce katman sayısı/bayrak/ad eşitlenir (`layers.sync`: fazlalar silinir, eksikler eklenir), sonra parametreler yazılır. Katman parametreleri katman kimliği + sapma olarak durur ve kimlikler her Cinema 4D'de farklıdır (526336, 526848, ...; silinen katmanlar boşluk bırakır): bu yüzden anahtarlar **katman sırasına** çevrilerek taşınır (`LAYER_BASE + 512 × sıra`, `to_canonical`/`to_local`), böylece yankı da oluşmaz. API R17.032+ (yoksa `layers.supported` yanlış döner, eski davranış). Gerçek Cinema 4D kontrolü: `c4dpy tests/c4d_real/layers_check.py`.
- **Mesaj boyutu:** `protocol.chunk_ops` toplu işlemleri ve anlık görüntü parçalarını sayı ve tahmini bayta (8 MB) göre böler; büyük bir dosya tek başına gider.
- **Bilinen sınırlar:** Layer shader'ın iç katman listesi, düğüm tabanlı (node) malzemeler ve ses/video dosyaları taşınmaz; kod taşıyan shader'lar (Python) yerel kalır.

### Seçim şekli ve hareket akıcılığı (Aşama 7, canlı geri bildirim)

- **Seçilen nesnenin kendi şekli:** Kutu yerine nesnenin gerçek kenarları kullanıcının renginde çizilir (piramit piramit gibi). `presence.object_outline`: nesnenin (deform) önbelleğindeki çokgen nesneleri toplar, benzersiz kenarları çıkarır, noktaları yerel uzayda saklar ve her çizimde yalnızca matrisle dünya uzayına çevirir. Mesh yalnızca nesnenin `GetDirty(DATA|CACHE)` değeri değişince yeniden okunur. Boş nesne, spline ve büyük mesh (6000 çokgen / 9000 kenar üstü) için kutuya düşer. İsim etiketi yine nesnenin tepesinde. Aynı renkte çizgiler tek `SetPen` ile çizilir.
- **Nesne hareketi:** Eski kayma her güncelleme için smoothstep kullanıyordu; hız her güncellemede sıfıra düşüyor ve bu kasma gibi görünüyordu. Şimdi nesne bir güncelleme aralığı kadar geriden gösterilir ve her alınan pozdan bir sonrakine **doğrusal** hareket eder (`tween.TransformTweens`, bölüt kuyruğu): hız güncellemeler arasında sabit kalır. Geç/erken gelen güncellemeler kuyruğa eklenir, 0,25 sn'den fazla birikirse kuyruk atılır.

### Gerçek C4D'nin bulduğu hata (Aşama 7)
Kayıtlı olmayan bir nesne/tag tipi oluşturulmak istenince C4D normal bir hata değil **çıplak `BaseException`** fırlatır; `except Exception` bunu yakalamaz. Ağdan gelen bilinmeyen bir tip (kurulu olmayan plugin, daha yeni C4D) bütün senkronizasyon turunu bozabilirdi. Çözüm: `compat.allocate()` ve sınır noktalarında `BaseException` yakalama (`KeyboardInterrupt`/`SystemExit` hariç, `core.compat.is_fatal`). Gerçek C4D senaryosuna bilinmeyen tip adımı eklendi.

## 7f. MCP sayfası: yapay zekâ asistanı canlı Cinema 4D'yi kullanabilir (Aşama 8, geliştirme aracı)

Maxon'un kendi MCP sunucusu yalnızca 2026.4+ içinde var; 2023 ve R19'da yok. Biz aynı işi **Edit → Preferences → MCP** sayfası + yerel bir sunucu ile R19'dan itibaren sağlıyoruz. Sayfa, Maxon'un 2026'daki sayfasının düzenini izler (SDK'daki `mcpadapterprefs.res` tanımı ve ekran görüntüleri incelendi): **Server, Clients, Security, Toolset** bölümleri, düğmeler, istemci açılır listesi, açarken onay sorusu. Amaç Collab'ın kendisi değil, geliştirme: Claude Code gibi bir uygulama probe, test ve sahne okumayı elle beklemeden canlı C4D'de yapabilsin. Collab'ın (ağ üzerinden) oturumuyla ilgisi yoktur ve onun güvenlik kurallarını (kod taşıyan tipler senkronize edilmez) değiştirmez.

```
AI uygulaması ──stdio──► mcp_stdio_bridge.py ──127.0.0.1──► core/rpc.RpcServer (soket thread'i, sadece kuyruğa yazar)
(Claude Desktop/Code,    (sistem Python'u)                          │ notify → SpecialEventAdd(MCP_MESSAGE)
 Codex, Cursor...)                                                  ▼
                              McpMessageData (ana thread, 50 ms timer) ► mcp_service.execute ► mcp_tools.*
```

### Sayfa (`c4d_adapter/mcp_prefs.py`, `res/description/mcpprefs.*`)
`PreferenceData` (R19+; daha eski sürümde sayfa atlanır, Collab yüklenir). Değerler world container'da (`MCP_CONTAINER`).

| Bölüm | Alan | Not |
|---|---|---|
| **Server** | Allow MCP Server | Varsayılan **kapalı**. Açarken onay sorusu (okunabilecekler, Python uyarısı, sağlayıcının veri politikası); Hayır = kapalı kalır. Program açılırken kendiliğinden döner, soru sorulmaz |
| | Status, Host (127.0.0.1) | Bilgi alanları (soluk) |
| | Port (1024-65535, 47921) | Değişince sunucu yeni porta geçer |
| | Auth Token + Show Token | Alan maskeli (`****…` + son 4 karakter); Show Token açıkken tam. Yazılan atılır |
| | Copy Token, Regenerate Token | Düğme. Yeni token yapılandırma dosyasına yazılır: AI tarafında bir şey değişmez |
| | Request Timeout (s) | 30 s, en az 0,1. **Gerçek anlamı:** komut ana thread'i bu kadar bekletirse (C4D render/uzun betik) **çalıştırılmaz**, "timed out" döner; geç kalmış bir komut sonradan çalışmaz |
| **Clients** | MCP Client | Claude Desktop, Claude Code, Codex, Cursor, Other |
| | Client Status, Config File | "Set up" / "Not set up" / "Not installed" / "Set up by hand" (2 sn önbellek: dosyalar büyük olabilir) |
| | Update Selected Client | Onaylı; yedek alır; yalnızca `cinema4d` girdisini ekler/değiştirir. Claude Code: komutu panoya koyar (terminalden kurulur); Other: Copy Config'e yönlendirir |
| | Remove from Client, Copy Config | Aynı kurallar; Copy Config her istemci için kendi biçimini kopyalar (JSON `mcpServers`, Codex TOML bloğu, `claude mcp add` satırı) |
| **Security** | Allow Python Execution (run_script) | Varsayılan **kapalı**, her çağrıda yeniden okunur |
| | Log Incoming Calls | Varsayılan açık (denetim günlüğü) |
| | Show Rejected Calls in Console | Reddedilen çağrıları Python konsoluna yazar |
| **Toolset** | Basics, Hierarchy, Entities, System | Kapatılan grubun araçları AI'a listelenmez (istemci yeniden başlayınca) ve çağrılırsa **hemen** reddedilir |

Maxon'un sayfasından **bilerek alınmayanlar:** *Allow Remote Connections / Allowed IPs / Browser Client Origins* (uzak bağlantı şifresiz olurdu ve ihtiyaç yok), *Allow Python-Bearing Operations* (Python taşıyan nesne oluşturan aracımız yok).

**Düğme ve açılır liste Preferences sayfasında çalışır:** ilk sürümde "Preferences sayfalarında düğme olmaz" demiştik; SDK iskeletinin sınırıydı, API'nin değil. Düğmeler `Message` içinde `MSG_DESCRIPTION_COMMAND` ile (`data["id"][0].id`) yakalanır (`PreferenceData`, `NodeData`'dan türer), açılır liste `CYCLE`. Gerçek 2023 kaynak ayrıştırıcısı `res_check.py` ile doğrulandı. **Doğrulanamayan:** düğme tıklamasının gerçekten `Message`'a ulaşması (c4dpy'de pencere yok), `CHECKLIST.md` 10. bölüm.

**Değer kimlikleri:** 1000-1002 ve 1005 ilk sürümdeki kayıtlı değerler olarak anlamlarını korur (kullanıcı tercihlerinde saklanıyor olabilir); yeni her şey 1100'den; 1003, 1004, 1006 hiç saklanmadı ve kullanılmaz.

### İstemci kurulumu (`core/mcp_clients.py`)
- **Python bulma:** AI uygulamaları masaüstünden başlar ve kabuğun PATH'ini görmeyebilir; bu yüzden gerçek bir kurulumun **mutlak yolu** yazılır: PATH'te (Microsoft Store'un `WindowsApps` kısayolu **hariç**: açılırsa Store'a götürür; bu makinede `python`/`python3` böyle) → bilinen kurulum yerleri (`%LOCALAPPDATA%\Python\pythoncore-*`, `Programs\Python\Python3*`, en yeni sürüm) → `py -3` → `python3`.
- **Dosyalar:** Claude Desktop **4.x** her verisini (ayarlar, oturum, bu yapılandırma) `%LOCALAPPDATA%\Claude-Data\claude_<id>\claude_desktop_config.json` içinde tutar ve **eski konumları okumaz** (canlı denemede bulundu: Store paketinin Roaming klasörüne yazılan girdi hiç yüklenmedi ve bağdaştırıcılar listesinde görünmedi; bu yüzden artık ilk aday bu dosyadır). Eski sürümler: **Microsoft Store paketi** (`%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\`, `%APPDATA%\Claude`'u görmez) ya da `%APPDATA%\Claude\claude_desktop_config.json`; Cursor `~/.cursor/mcp.json`; Codex `~/.codex/config.toml` (`CODEX_HOME` geçerli); Claude Code `~/.claude.json` (yalnızca durum için okunur, yazılmaz).
- **Güvenlik kuralları:** değişiklikten önce `.bak-<zaman>` yedeği; geçici dosya + yer değiştirme; **ayrıştırılamayan dosyaya hiç yazılmaz**; yalnızca `cinema4d` girdisi eklenir/değişir/silinir, gerisi aynen kalır; Codex'te işaretli blok (`# >>> C4D Collab MCP ... <<<`), elle yazılmış `[mcp_servers.cinema4d]` girdisine dokunulmaz; uygulama kurulu değilse (yapılandırma klasörü yok) hiçbir şey oluşturulmaz.
- **Pano:** `core/clipboard.py` (Windows'ta ctypes ile Unicode; Cinema 4D Python'u panoyu okuyabilir ama yazamaz). c4dpy'de `GetStringFromClipboard` hep `None` döner (arayüzsüz), testler panoyu PowerShell ile okur.

### Sunucu ve güvenlik
- Varsayılan kapalı; yalnızca `127.0.0.1`'e bağlanır ve yalnızca yerel bağlantı kabul eder; rastgele 128 bitlik token, ağda HMAC-SHA256 (token gitmez), giriş öncesi 4 KB / 5 sn, 5 yanlış girişte 60 sn kilit, aynı anda en çok 4 istemci ve 8 bekleyen komut, sonuç 4 MB sınırı; soket thread'i hiçbir komut çalıştırmaz (değişmez kural 4); yalnızca JSON; her komut `c4d_collab_mcp_audit.log` dosyasına yazılır (token yazılmaz, testle doğrulandı); kapalıyken yapılandırma dosyası (token içerir) silinir. `run_script` ayrı bir izne bağlıdır; çalışırken C4D yanıt vermez (sınırlanamaz) ve `sys.exit()` betiği bitirir, Cinema 4D'yi değil.
- **Yapılandırma dosyası** (`c4d_collab_mcp.json`, açıkken): port, token, `timeout`, `enabled_tools`, köprünün yolu ve AI uygulamasına yazılan girdi. Köprü bunu her çağrıda okur; araç listesini `enabled_tools`'a göre süzer (Cinema 4D kapalıyken hepsini listeler).
- **Araçlar:** `ping`, `get_info`, `get_hierarchy`, `get_object`, `get_log`, `run_script`.

### Testler
`tests/core/test_rpc.py` (taşıma, kimlik doğrulama, kilitleme, sınırlar, zaman aşımı, köprü alt süreci ve araç süzme), `tests/core/test_mcp_clients.py` (istemci dosyaları, yedek, bozuk dosya, Codex bloğu, Python bulma, pano), `tests/adapter/test_mcp.py` (sayfa, kayıt, onay, düğmeler, gölgeleme, token maskeleme, araç grupları, zaman aşımı, python izni, denetim günlüğü), `tests/c4d_real/mcp_check.py` (gerçek 2023 API'si: world container, sayfa nesnesi, **gerçek pano**, istemci yazma, sunucu, her araç), `tests/c4d_real/res_check.py` (**gerçek 2023 kaynak ayrıştırıcısı** `.res/.h/.str` dosyalarını okur: 4 grup, 28 parametre, veri tipleri, adlar, açılır liste girdileri). Testlerin bulduğu hatalar: Türkçe Windows + Python 2.7'de işletim sistemi hata metni `str()` içinde `UnicodeDecodeError` veriyordu (köprü `error_text` kullanıyor); script içindeki `sys.exit()` ana thread'e kadar yükseliyordu (artık betiği bitiriyor); bu makinede `python` Store kısayolu çıktı (bulucu atlıyor).
- **Doğrulanamayan (GUI gerektirir):** sayfanın Preferences listesindeki yeri (`PAGE_PARENT`/`PAGE_SORT`, `mcp_prefs.py`), düğmelerin `Message`'a ulaşması, R19'daki `PreferenceData` imzaları. `tests/c4d_manual/CHECKLIST.md` 10. bölüm.

## 7g. Sürüm uyumluluğu: R17'den 2026'ya (Aşama 8)

Hedef R17, R18, R19, R20, R21, R22, R23, 2023, 2024, 2025, 2026. Elimizde gerçek olarak yalnızca **R19** ve **2023** (ve lisans girişi bekleyen 2024) var. Diğerlerinde kodun **çalıştığını** değil, **kullandığı API'nin o sürümde var olduğunu ve yedek yolların test edildiğini** kanıtlayabiliriz. İkisi ayrı tutulur:

| Sürüm | Python | Durum |
|---|---|---|
| **R19** | 2.7 | **Gerçek testle doğrulandı** (iki süreçli senaryo, elle denetim listesi) |
| **2023** | 3.9 | **Gerçek testle doğrulandı** (c4dpy senaryoları, MCP sayfası canlı) |
| R17, R18 | 2.7 | API uyumu belgeden doğrulandı + sahte sürüm profili testi; **gerçek denenmedi** |
| R20, R21, R22 | 2.7 | profil testi (R20, R21 profilleri; R22 = R21); **gerçek denenmedi** |
| R23 | 3.7 | profil testi + Python 3.7 statik denetim; **gerçek denenmedi** |
| 2024 (3.11) | 3.11 | **gerçek denenmedi** (makinede kurulu, `c4dpy` ilk kullanımda lisans girişi ister) |
| 2025, 2026 | 3.11+ | R23 profiliyle aynı API yüzeyi (2026 SDK örnekleri ve 2023 belgeleri); **gerçek denenmedi** |

### Neyi, nasıl doğruluyoruz (dört katman)
1. **API envanteri** (`tests/compat/api_inventory.py`): eklentinin kullandığı her Cinema 4D adı (AST taraması: 60 modül düzeyi sembol, 174 nesne yöntemi, 95 korumalı ad). 2023'te hepsi gerçek `c4dpy` ile kontrol edildi (tek eksik `c4d.storage.ByteSeq`, R19'a özgü ve korumalı); R19'da bağlama kitaplığında hepsinin adı geçiyor.
2. **Sürüm bilgisi** (`tests/compat/api_matrix.py`): her ad için "hangi sürümden beri var". Kaynak Maxon'un Python API belgeleri (2023.2 sınıf sayfalarındaki "New in version ..." notları: belge yalnızca kendi taban sürümünden yeni olanı işaretler, notu olmayan R16 ya da daha eski demektir), 2026 SDK örnekleri ve R19/2023'te ölçülenler. `test_api_matrix.py` **yeni bir API adı sınıflandırılmadan testin geçmesine izin vermez** ve R17'den yeni her adın yanında koruma olduğunu denetler.
3. **Sürüm profilleri** (`tests/adapter/profiles.py`, `test_profiles.py`): sahte `c4d` modülü R17, R18, R19, R20, R21, R23 gibi davranır (olmayan yöntemler gerçekten `AttributeError` verir, bayrak adları değişir); her profilde 9 test sınıfı (senkronizasyon, düzenleme, rig, seçim, mesh, animasyon, kilit, Pose Morph, malzeme) ve eklentinin yüklenmesi çalışır. Python 2.7 ve 3.x'te.
4. **Python denetimi** (`tests/compat/python_audit.py`): kaynağı (çalıştırmadan) 2.7 ve 3.7'nin okuyamayacağı sözdizimi (f-string, `:=`, anotasyon, `async`, `nonlocal`, `yield from`, yalnızca-anahtar-kelime parametre...), korumasız yeni standart kütüphane adları (`time.monotonic`, `shutil.which`, `subprocess.run`, `pathlib`...), eksik `__future__` satırı ve `coding` satırı için tarar. Kendi kendini sınar (`test_python_audit.py`: kötü örnekleri yakaladığı gösterilir).

Python yorumlayıcıları: 2.7.9 (R19'un kendi yorumlayıcısı), 3.9.1 (2023'ün kendi yorumlayıcısı, bağımsız çalışır), 3.14 (makinenin). 3.7 ve 3.11 yok; 3.7 için yukarıdaki statik denetim.

### Belgeden çıkan gerçek boşluklar ve yedek yolları
| API | Ne zaman geldi | Eksik sürümlerde ne olur | Yer |
|---|---|---|---|
| `BaseDocument.GetHDirty` | R19 | R17/R18'de belge sayaçları yok: anlık görüntü her çağrıda değişir, sahne **her tick taranır** (tick'in kendi dinlenmesi ana thread'i ~üçte biriyle sınırlar). Çökme yerine yavaş ama doğru | `compat.doc_dirty`, `HAS_HDIRTY` |
| `BaseSelect.GetRange` | R19 | Seçim, bayraklardan (`GetAll`) okunur; aynı aralıklar çıkar | `geometry.dump_selection` |
| `BaseDraw.DrawHUDText` | R18.020 | R17'de seçilen nesnenin isim etiketi çizilmez, renkli çerçeve çizilir | `presence.draw_overlay` |
| `CAWeightTag.SetWeightMap` | R21 | R17-R20'de cilt ağırlıkları nokta nokta (`SetWeight`) yazılır: büyük mesh'te yavaş, sonuç aynı | `rig.load_weights` |
| `RegisterPreferencePlugin`, `PreferenceData` | R19 | R17/R18'de MCP sayfası yok (ve boşuna zamanlayıcı da kayıt edilmez); Collab etkilenmez | `mcp_prefs.available`, `c4d_collab.pyp` |
| `AddUniqueID`/`FindUniqueID` | R16.021 | R17'de var (belge); `ByteSeq` gerekirse yalnızca TypeError'da denenir | `compat.write_uid` |

Ayrıca: R17/R18/R19'da `DESCFLAGS_*_NONE` adları yok (`_0` kullanılır), ayrıntılı `HDIRTYFLAGS_*` R19'da yok (ölçüldü): ikisi de zaten `compat`'ta. Başlangıçta günlüğe `Capabilities: ...` satırı yazılır (hangi yedek yolların kullanıldığı); her sürümden gelen bir rapor önce buraya bakılarak okunur.

### Kanıtlanamayanlar (gerçek sürüm gerekir)
- **Davranış:** hangi düzenlemenin hangi sayacı oynattığı (R17-R22, 2024+), çizim sırası, `GetDeformCache` gibi sürüme özgü ayrıntılar. Gerçek sürümü açabilen biri `tests/c4d_manual/spike_probe.py` ve `CHECKLIST.md`'yi çalıştırıp sonucu iletmeli.
- **Python 2.7 dizgi kuralı** (`str`/`unicode`): R17-R22'de sahte modül zorluyor, gerçek sürümlerde yalnızca R19'da ölçüldü.
- **2024-2026:** belge ve SDK örnekleri API yüzeyinin değişmediğini gösteriyor, ama 2025/2026'da yeni kaldırılan bir şey olursa yalnızca gerçek çalıştırma yakalar. 2024 için `c4dpy`'yi bir kez etkinleştirmek yeterli.

### Yeni API kullanmadan önce
`py tests/compat/api_inventory.py` ile kullanılan adları gör; `py -m unittest discover -s tests/compat` yeni ad için başarısız olur ve ne yapılacağını söyler. Maxon belgesinde sınıf sayfasında "New in version" notuna bak; R17'den yeniyse yedek yol yaz, `api_matrix.NEWER`'a ekle, `profiles.py` içinde o sürümde yok say ve bir test ekle.

## 7b. Adaptör — özet
- `bridge`: MessageData plugin'i. `SpecialEventAdd` ve timer ile tetiklenir, ana thread'de çalışır.

## 8. Test
- `tests/core`: çekirdek (protokol, model, kilitler, gerçek sunucu+istemci senaryoları, güvenlik, oturum yöneticisi).
- `tests/adapter`: C4D adaptörü, **sahte bir `c4d` modülüyle** (`fake_c4d.py`). `.pyp` dosyasının yüklenmesini, Python 2'deki `str`/`unicode` kuralını, R19'un `ByteSeq` davranışını ve panelin Host/Join/Leave akışını C4D açmadan test eder. Gerçek API davranışını değil, bizim mantığımızı doğrular.
- `tests/c4d_real`: **gerçek C4D API'si** üzerinde iki süreçli senaryo (host + misafir; sahne gönderme, düzenlemeler, hiyerarşi, silme, geometri, link, kod tiplerinin engellenmesi, yankı olmaması). Taraflar birbirleriyle oturumun kendi presence mesajlarıyla senkronize olur.
  - `py tests/c4d_real/run.py` → iki gizli (GUI'siz) C4D 2023 `c4dpy` süreci, tamamen otomatik.
  - `py tests/c4d_real/run.py --manual-peer guest|host --label r19` → bir rol `c4dpy`'de, diğeri GUI'li bir C4D'de **Plugins → C4D Collab Self-Test (dev)** ile (c4dpy olmayan R17–R20 için). Bu komut sadece `plugin/c4d_collab/selftest.enabled` dosyası varsa görünür (dosya repo yolunu içerir, git'e girmez).
  - R19'un `Commandline.exe`'si ve `CINEMA 4D.exe -nogui` bu makinede yönetici hakkı istiyor, bu yüzden R19 tarafı tek tıkla çalıştırılıyor.
- `tests/c4d_manual/CHECKLIST.md`: gerçek C4D'de elle yapılan testler (fareyle sürükleme, Ctrl+Z gibi otomatikleştirilemeyenler).
- Testler sadece stdlib `unittest` kullanır, bu yüzden her Python'da çalışır (`tests/adapter` için de aynı komutlar, `-s tests/adapter` ile):
  - Python 3: `py -m unittest discover -s tests/core -v`
  - Python 2.7 (R19'un kendi Python'u): `"C:/Program Files/MAXON/Cinema 4D R19/resource/modules/python/Python.win64.framework/python.exe" -m unittest discover -s tests/core`
  - C4D 2024 `c4dpy` (Python 3.11): ilk kullanımda bir kez Maxon lisans girişi ister.
- C4D içi ölçüm scripti: `tests/c4d_manual/spike_probe.py` (Script Manager'dan çalıştırılır).

## 9. Ölçümler (Aşama 0)
Kaynak: `tests/c4d_manual/spike_probe.py`. Programdan yapılan düzenlemelerle ölçüldü; fareyle yapılan düzenlemeler Aşama 2'de ayrıca doğrulanacak.

| C4D sürümü | Python | Durum |
|---|---|---|
| 2023.1.3 | 3.9.1 | ✅ Tamamlandı |
| R19.024 | 2.7.9 | ✅ Tamamlandı (kaydet/yükle testi bir test hatası yüzünden yapılamadı, Aşama 2 kontrol listesinde doğrulanacak) |
| 2024 | 3.11 | Bekleniyor |

### Sürüm farkları
- **R19'da belge düzeyinde sadece `HDIRTYFLAGS_ALL` var.** Ayrıntılı bayraklar (`OBJECT`, `OBJECT_MATRIX`, `OBJECT_HIERARCHY`, `ANIMATION`, `TAG`) R19'da yok. `compat` modülü bunları sürüme göre eşler: yoksa `ALL` kullanılır ve "her şey değişmiş olabilir" kabul edilir (tam tarama). Ölçülen hızlarla bu R19'da da yeterince hızlı.
- **R19'da `AddUniqueID` `str`/`bytearray` kabul etmiyor**, `c4d.storage.ByteSeq` istiyor. Çalışan yöntem: `seq = c4d.storage.ByteSeq(None, 16)`, ardından baytları tek tek `seq[i] = chr(b)` ile yazmak. Geri okuma doğru, kopyalanan nesne ID'yi taşımıyor (2023 ile aynı). `compat.write_uid()` önce `bytes` dener, olmazsa bu yöntemi kullanır.
- **Python 2'de (R17–R22) c4d API'si `unicode` metin kabul etmiyor**, `str` istiyor (ör. `SaveDocument` dosya yolu). `unicode_literals` kullandığımız için c4d'ye giden her metin `compat.native_str()` üzerinden geçer. Bu kural sadece adaptör katmanını ilgilendirir.
- Yedek fikir olarak denenen "ID'yi nesnenin BaseContainer'ında saklamak" çalışıyor ama ID kopyalanan nesneye de geçiyor. Bu yüzden kullanılmayacak, `AddUniqueID` kullanılacak.
- `GetMarker` iki sürümde de Python'da yok; kullanılmayacak.

### Ortak bulgular (R19 ve 2023 aynı davranıyor)
| Düzenleme | Nesnenin değişen sayaçları | Belgenin değişen sayaçları (`GetHDirty`) |
|---|---|---|
| Taşıma / döndürme | `MATRIX` | `OBJECT_MATRIX` |
| Parametre (ör. küp boyutu) | `DATA` | `OBJECT` |
| Yeniden adlandırma | `DATA` | `OBJECT` |
| Seçim | **hiçbiri** | **hiçbiri** |
| Ebeveyn değiştirme | **hiçbiri** | `OBJECT_HIERARCHY` |
| Tag ekleme | `DATA, MATRIX` | `OBJECT, OBJECT_MATRIX, TAG` |
| Keyframe ekleme/değiştirme/zamanını kaydırma | nesne ve CTrack: **hiçbiri**, CCurve: `CHILDREN` | `ANIMATION` |
| Frame değiştirme | hiçbiri | hiçbiri |
| Nokta taşıma | `DATA` | `OBJECT, TAG` |
| Extrude (topoloji değişimi) | `DATA, SELECT` | `OBJECT, TAG` |

(Belge sütunu 2023'e aittir; R19'da hepsinde sadece `ALL` değişir.)

**Mimariye etkileri (tracker tasarımı):**
1. **İki kademeli tarama:** Önce belgenin `GetHDirty` sayaçlarına bakılır (çok ucuz). Hiçbiri değişmediyse o tick'te hiçbir şey taranmaz. R19'da bu kontrol `ALL` ile yapılır.
2. `OBJECT` / `OBJECT_MATRIX` / `TAG` değiştiyse nesnelerin `DATA`/`MATRIX` sayaçları taranır.
3. `OBJECT_HIERARCHY` değiştiyse nesne sayaçlarına güvenilmez: sahnedeki `parent/prev` yapısı mirror ile karşılaştırılır.
4. `ANIMATION` değiştiyse sadece CCurve'lerin `CHILDREN` sayaçları taranır.
5. **Seçim** ve **geçerli frame** sayaçlara yansımıyor: timer içinde `doc.GetActiveObjects()` ve `doc.GetTime()` ayrıca kontrol edilir (kilit ve presence için).

**UniqueID:**
- `AddUniqueID` ile yazılan ID kaydetme/yükleme sonrası korunuyor ✅.
- **Kopyalanan nesne ID'yi taşımıyor** (`GetClone`). Yani ID'siz nesne = yeni nesne demek; kopya tespiti için ek bir şey gerekmez.
- `AddUniqueID` aynı uygulama ID'si için üzerine yazmıyor, yeni bir kayıt **ekliyor** (sayı 2 oldu). Okurken en son yazılan dönüyor. Kural: bir nesneye ID yalnızca bir kez yazılır.

- Modelleme: nokta taşıma ve extrude nesnenin `DATA` sayacını artırıyor. Nokta ve poligon sayısına bakılarak "sadece noktalar değişti" ile "topoloji değişti" ayrımı yapılabilir.

### Animasyon ölçümleri (Aşama 5, 2023, `tests/c4d_manual/probe_animation.py`, c4dpy ile otomatik)
| Düzenleme | Nesne / Track / Curve sayacı | Belge sayacı |
|---|---|---|
| Key ekleme, silme, değer, zaman, interpolasyon | – / – / `CHILDREN` | `ANIMATION` |
| **Tangent değeri** | – / – / – | **–** |
| **Track ayarı (after-loop vb.)** | – / – / – | **–** |
| Kare değiştirme (`SetTime`) | – | – |

Diğer bulgular: değer track tipi `CTbase` = 5350, `CCurve.AddKey` bir dict (`key`, `nidx`) döndürür, long/bool parametre track'leri de `GetValue()` ile sayısal değer kullanır.

### Modelleme ölçümleri (Aşama 6, 2023, `tests/c4d_manual/probe_modeling.py`, c4dpy ile otomatik)
| Düzenleme | Sayaç |
|---|---|
| `SetPoint` | nesne `DATA`; belge `OBJECT, TAG` |
| **Seçim tag'inde seçim değişikliği** | **hiçbir sayaç** |
| UVW `SetSlow` | UVW tag'i `DATA`; belge `TAG` |
| Aktif poligon seçimi (`GetPolygonS`) | nesne `SELECT` (senkronize edilmez) |
| Spline `SetPoint` | nesne `DATA`; **`SetTangent` ve `SetSegment`: hiçbir sayaç**; tür değişikliği `DATA` |

Diğer bulgular:
- `SelectAll(a, b)` hiçbir şey seçmedi; seçimi **`SetAll(liste)`** ile yazıp `GetCount` ile doğruluyoruz (olmazsa `Select(i)` döngüsü). Okuma `GetSegments`/`GetRange(i, sayı)` ile (kapalı aralıklar).
- `MakeTag(Tuvw)` sonrası UVW tag'inde 0 eleman var; `ResizeObject(aynı boyut)` tag'i poligon sayısına boyutlandırır. Sonra C4D tag'i poligon sayısıyla senkron tutar (extrude: 4→8 poligon, UVW 8).
- `ResizeObject` mevcut nokta ve UV verisini korur (büyütme ve aynı boyut).
- `BaseObject(Ospline)` boş doğar (0 nokta); `ResizeObject(nokta, segment)` sonrası `SetSegment` çalışır. Bezier türünde tangent sayısı = nokta sayısı.
- UV değerleri C4D'de **float32** saklanır; yazdığımızın aynısını okuyamayız, ama gönderen hep C4D'den okuduğu için tutarlı kalır (yankı yok).
- Hız (2023): 100.000 nokta okuma ~20 ms, poligon ~27 ms, yazma ~26 ms.

**Performans (nesne başına 2 sayaç okuma, tam tarama):**

| | 1.000 nesne | 10.000 nesne |
|---|---|---|
| R19 | 0,56 ms | 6,13 ms |
| 2023 | 0,51 ms | 4,86 ms |

Hedef 1.000 nesne başına 10 ms'nin altıydı; gerçek değer bunun yaklaşık 20 katı daha hızlı ✅.
