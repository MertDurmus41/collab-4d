# Multi-User ve Mixer: nasıl çalışıyorlar, C4D Collab'a ne taşınır?

Tarih: 2026-10-28. İncelenen kaynaklar:

| Proje | Depo | Sürüm | Boyut |
|---|---|---|---|
| Multi-User (Swann Martinez / slumber) | gitlab.com/slumber/multi-user | 74c20b5 (Eyl 2026) | 11.6k satır Python |
| replication (Multi-User'ın ağ/veri çekirdeği) | gitlab.com/slumber/replication | a3d36bc (Eyl 2026) | 3.6k satır |
| Mixer (Ubisoft Animation Studio) | github.com/ubisoft/mixer | 5f6edaf (Ağu 2022, arşiv) | 20k satır |

## 0. REA hakkında

`morluto/rea` derlenmiş programları (Hopper/Ghidra/IDA ile native, JS/Electron, .NET, APK, PyInstaller paketleri, web siteleri) kaynak kodu olmadan çözmek için bir MCP aracı. İki eklenti de **açık kaynak Python**; çözülecek bir ikili dosya yok, kaynak doğrudan okunabiliyor. REA'nın bu iş için bir analizcisi yok (düz Python kaynağı desteklemiyor). Kurulumu (`npx rea-agents setup`) ajan yapılandırmasını değiştirdiği için kurmadım. "Tersine mühendislik" burada kaynağı satır satır okuyup veri akışını çıkarmak demek; aşağıdaki her iddia ilgili dosyaya dayanıyor.

## 1. Multi-User (replication + multi_user)

### 1.1 Katmanlar
```
Blender ──► multi_user/bl_types/bl_*.py   (her veri tipi için dump/load/resolve/resolve_deps)
             multi_user/handlers.py        (depsgraph_update_post → commit + push)
             multi_user/timers.py          (bpy.app.timers: ağ dinleme, uygulama, kilit, presence)
        ──► replication/porcelain.py      (git benzeri: add / commit / push / apply / lock / unlock / rm)
             replication/repository.py    (düğüm grafiği: uuid → Node{data, owner, dependencies, state})
             replication/net.py           (TCP, uzunluk önekli çerçeve; eskiden ZeroMQ'ydu)
        ──► replication/server.py         (ayrı süreç: komut thread'i + veri thread'i + TTL/ping thread'i)
```

### 1.2 Veri modeli: git benzetmesi
- **Node** = bir Blender datablock'u (`uuid, owner, dependencies, data(dict), state`). Durumlar: `ADDED → COMMITED → (push) → UP`, uzaktan gelen: `FETCHED → (apply) → UP` (`replication/constants.py`).
- **Bağımlılık grafiği**: Nesne → mesh → malzeme → doku. `resolve_deps()` her tip için bağımlılıkları verir. `commit` ve `apply` ters BFS ile **önce bağımlılıkları** işler (`repository.rbfs_from`). Bu bizim `KIND_ORDER` + ebeveyn-önce sıramızın daha genel hali.
- **Commit** = Blender'dan `dump()` → son gönderilen dict ile `deepdiff` farkı (`protocol.compute_delta`). `use_delta=True` olan tipler (nesne) sadece farkı (`Commit` nesnesi) gönderir, diğerleri tamamını. Nesne tipi, farkı hızlandırmak için önce üst düzey anahtarları `!=` ile karşılaştırıp deepdiff'i yalnızca değişen dallarda çalıştırıyor (`bl_object.compute_delta`). Bizim `diff.py` + parça (chunk) yaklaşımıyla aynı fikir.
- **Apply** sırası `ApplyTimer` içinde (ana thread, `bpy.app.timers`): FETCHED olan her düğüm `load()` edilir; bazı tipler ebeveynlerini/çocuklarını yeniden yükletir (`bl_reload_parent`, `bl_reload_child`). Bizim `Applier._reload_dependents / _reload_weights / _relink` ile aynı sorun, aynı çözüm.

### 1.3 Değişiklik algılama ve yankı
- Blender bize hazır bir sinyal veriyor: `depsgraph.updates` hangi ID'nin değiştiğini söylüyor. Multi-User bunu dinliyor (`handlers.on_scene_update`).
- **Yankı bastırma**: uzaktan gelen bir güncelleme uygulanınca uuid `shared_data.session.applied_updates` listesine eklenir; bir sonraki depsgraph olayında bu uuid'ler atlanır.
- **En önemli satır** (`handlers.py`):
  ```python
  if node and (node.owner == session.repository.username or check_common):
      porcelain.commit(...); porcelain.push(...)
  ```
  Yani **yalnızca kilidi bende olan (seçtiğim) düğüm gönderilir**. Kilitsiz (COMMON) düğümler, tipi `bl_check_common=True` değilse (sahne, koleksiyon gibi) gönderilmez. Nesnelerde `bl_check_common = False`. Rig'in hareket ettirdiği, kimsenin seçmediği bir nesne **hiç gönderilmez**.

### 1.4 Kilitler
- Seçim = kilit (`DynamicRightSelectTimer`, 100 ms): yeni seçilenler `lock`, bırakılanlar `unlock`, `affect_dependencies=True` ile mesh/malzeme gibi bağımlılıklar da kilitlenir.
- **Başkasının kilitli nesnesi seçilemez yapılır**: `obj.hide_select = True`. Yani Multi-User düzenlemeyi **geri almıyor, en baştan engelliyor**.
- Kilitsiz bir nesneyi başkası değiştirse bile yerel değişiklik gönderilmez (1.3), dolayısıyla "kilitli nesneyi değiştirdin, geri alıyorum" diye bir yol **hiç yok**.
- Sunucu yetki **denetlemiyor**: `ServerData.run` gelen her güncellemeyi kaydedip herkese yayınlıyor. Yetki sadece istemcide (`assert_modification_rights`). Bizde sunucu da denetliyor; bu konuda bizimki daha sağlam.
- Kopan kullanıcının kilitleri sunucuda `COMMON`'a döner (`disconnect_client`). Bizde de aynı.

### 1.5 Geç katılan
- Sunucu, düğüm grafiğinin tam bir kopyasını tutar. İlk bağlanan istemci tüm sahnesini sunucuya yükler (`ServerSnapshot INIT/SET/DONE`).
- Sonra katılan önce **katalog** (uuid listesi) alır, sonra her düğümü tek tek `GET` ile ister (`Snapshot` komutu). Bu sırada gelen canlı güncellemeler `_snapshot_late_updates` kuyruğunda bekletilir.
- Bizde: sunucu `SessionMirror`'ı tutar, `snapshot_begin/chunk/end` ile parça parça gönderir, `seq` ile sıralar. Aynı model, daha az mesaj.

### 1.6 Rig nasıl taşınıyor? (bizim sorunun özü)
- **Armatür**: kemik yapısı EDIT modunda yazılır (`bl_armature.py`, `mode_set('EDIT')`).
- **Pose kemikleri**: `location / rotation / scale` (yerel giriş değerleri) ve kemik **constraint'leri** taşınır. Hesaplanmış matrisler taşınmaz.
- **Sürücüler (drivers)**: `bl_action.dump_driver/load_driver` sürücünün **tanımını** taşır: tip, `expression` (Python ifadesi bile olabilir), değişkenler, eğri. Blender, Python ifadeli sürücüleri "Auto Run Scripts" kapalıysa çalıştırmaz; güvenlik duvarı Blender'ın kendisi.
- **Shape key'ler (Blender'ın Pose Morph karşılığı)**: `dump_shape_keys`: her key bloğu için `name, mute, value, slider_min, slider_max, relative_key` + tüm nokta koordinatları (numpy dizisi) + shape key'lerin kendi animasyon verisi/sürücüleri. Yüklemede tüm key'ler silinip baştan kurulur.
- Sonuç: **misafir rigi kendisi değerlendirir.** Sadece girişler (kontrolcü konumu, slider değeri) ağdan gelir. Bu bizim "host hesaplar, misafir sonucu alır" yaklaşımımızın tam tersi.

### 1.7 Neden Blender'da kilit/rig çatışması olmuyor?
Blender'da sürücüler ve constraint'ler **orijinal veriye yazmaz**; depsgraph'ın değerlendirilmiş kopyasına (copy-on-write) yazar. Multi-User orijinali (`update.id.original`) okuduğu için rig değerlendirmesi hiç "değişiklik" gibi görünmez.
**C4D'de böyle bir ayrım yok**: XPresso, Constraint, IK ve Python tag'leri sonuçlarını doğrudan belgedeki nesnenin parametrelerine yazar ve sayaçları oynatır. Bizim tracker rig değerlendirmesini kullanıcı düzenlemesinden ayıramıyor. Dünkü "'Weight' is locked by Mert, your change was undone" döngüsünün asıl kökü bu.

### 1.8 Zayıf yanlar (taşımayacaklarımız)
- Ağdan gelen veri `pickle.loads` ile açılıyor (`objects.py`, `repository.loads`). Şifreyi bilen kötü niyetli bir eş, karşının bilgisayarında kod çalıştırabilir. Bizim "sadece JSON" kuralımız doğru.
- Sunucu tarafında yetki denetimi yok (1.4).
- Her depsgraph olayında `update_external_dependencies()` tüm resim düğümlerini yeniden commit ediyor (pahalı).

## 2. Mixer (Ubisoft)

### 2.1 Katmanlar
```
Blender ──► handlers_generic.send_scene_data_to_server (depsgraph_update_post)
             blender_data/*_proxy.py   (bpy.data'nın bellekteki ayna kopyası: BpyDataProxy)
             blender_data/filter.py    (hangi tip/özellik senkronize edilir: hariç listeleri)
             blender_data/specifics.py (tipe özel oluşturma/yazma kuralları)
        ──► broadcaster/client.py ── TCP ── broadcaster/apps/server.py (oda = mesaj günlüğü)
```

### 2.2 Ayna (proxy) ve fark
- `BpyDataProxy`, `bpy.data`'nın büyük kısmını **RNA yansıması** ile özyinelemeli olarak kopyalar. Her tip için ayrı kod yazmak yerine Blender'ın kendi özellik tanımları (`bl_rna.properties`) gezilir; istisnalar `filter.py`'de (`default_exclusions`).
- Depsgraph olayında: (1) ID düzeyinde fark (eklendi/silindi/yeniden adlandırıldı), (2) güncellenen ID'ler için **özellik düzeyinde fark** (`struct_proxy._diff`). Değişen alanlar `Delta` olarak gönderilir.
- Alıcıda gelen fark hem Blender'a hem **aynaya** uygulanır (`bpy_data_proxy.update_datablock`). Bu sayede bir sonraki karşılaştırmada fark çıkmaz: yankı kendiliğinden kaybolur. Ek olarak `skip_next_depsgraph_update` bayrağı uygulamanın tetiklediği depsgraph olayını atlar.
- **Bizim `shadow` + "son uygulanan canlı içerik" önbelleğimiz bu fikrin aynısı.** Mixer'ın genel RNA yansıması da bizim `params.dump` (description üzerinden genel serileştirme) yaklaşımımızla aynı. Doğru yoldayız.

### 2.3 Rig ve pose
- `filter.py`: `PoseBone.matrix`, `matrix_basis`, `head`, `tail`, `is_in_ik_chain`, `matrix_channel` **hariç**: "TRS'den hesaplanıyor" notuyla. Yani yalnızca giriş değerleri gider; hesaplanmış değerler gitmez.
- Shape key (`T.Key`) ayrı datablock olarak senkronize edilir; `extra_updates()` her güncellemede shape key'i ayrıca kontrol eder çünkü depsgraph bazen Key güncellemesini kaçırıyor (ölçülmüş tuzak, testi var: `TestShapeKey.test_rename_key`).
- **POSE modunda gönderim ertelenir**: `if bpy.context.mode == "POSE": delayed_updates.update(updates)` ("kontrolcüyü taşımak çok fazla güncelleme üretiyor"). Edit/paint modundaki mesh güncellemeleri de mod değişene kadar bekletilir. Canlılık yerine ağ yükünü azaltmayı seçmişler.
- Armatür EDIT modu geçişleri geri alınabilir "Command" nesneleriyle yapılır (`armature_proxy.Command`), aktif nesnenin modu sonra eski haline döner.

### 2.4 Çatışma ve geç katılan
- **Kilit yok.** Son yazan kazanır. Belgeleri açıkça "undo/redo desteklenmez, senkronizasyonu bozabilir" diyor.
- Sunucu her odada **tüm komutların günlüğünü** tutar. Yeni katılana önce bütün günlük gönderilir, sonra odaya alınır (`Room.add_client`, `_try_finish_sync`). Günlük şişmesin diye aynı yola ait art arda gelen "optimize" komutlar birleştirilir (`merge_command`: son komut aynı tip ve yoldaysa üzerine yazılır).
- Bizim sunucumuz günlük değil durum (mirror) tuttuğu için bu sorunu hiç yaşamıyor.

## 3. Üç sistemin karşılaştırması

| Konu | Multi-User | Mixer | C4D Collab (bugün) |
|---|---|---|---|
| Değişiklik sinyali | depsgraph.updates | depsgraph.updates | Belge/nesne dirty sayaçları (C4D'de depsgraph yok) |
| Neyi okur | Orijinal veri | Orijinal veri (filtreli) | Belgedeki canlı veri (rig sonuçları dahil) |
| Fark | deepdiff, üst düzey daraltma | Özellik düzeyinde ayna farkı | Patch diff + 128'lik parçalar |
| Yankı | applied_updates listesi | Ayna güncellenir + skip bayrağı | Shadow + sayaç kaydı + "son görülen" önbellek |
| Kim gönderir | **Sadece kilit sahibi** | Herkes | Kilitsiz veya kilidi bende olan her düğüm |
| Kilit | Seçim = kilit, başkasınınki seçilemez | Yok | Seçim = kilit, başkasınınki seçimden düşer; yetkisiz değişiklik geri alınır |
| Sunucu denetimi | Yok | Yok | Var |
| Rig mantığı | Tanımlar gider, herkes kendisi değerlendirir | Tanımlar gider, herkes kendisi değerlendirir | XPresso/Python yerel kalır, host'un sonuçları gider |
| Yüz (shape key / Pose Morph) | Tam taşınır | Tam taşınır | Taşınmıyor |
| Kod güvenliği | pickle (zayıf), Python sürücüleri Blender'ın Auto Run'ı ile korunur | JSON | JSON, kod taşıyan tipler hiç oluşturulmaz |
| Geç katılan | Sunucu grafiği, katalog + düğüm düğüm GET | Komut günlüğü yeniden oynatılır | Sunucu mirror'ı, parçalı snapshot |

## 4. C4D için çıkarımlar

### 4.1 Asıl ders: "girişleri taşı, herkes kendisi değerlendirsin"
İki eklenti de rig'in **tanımını** (sürücü, constraint, shape key) ve **girişlerini** (kontrolcü TRS, slider) taşıyor; hesaplanmış sonuçları taşımıyor. Bizdeki "karakter host'takinden biraz farklı duruyor" sorunu, sonuçların bir kısmını (XPresso'nun yazdıkları) taşıyıp bir kısmını (Pose Morph, XPresso'nun misafirde çalışmaması) taşımamaktan geliyor.

**XPresso kod değildir.** Bir XPresso etiketi düğümlerden ve bağlantılardan oluşan bir veri grafiğidir. Sadece içindeki **Python** ve **COFFEE** düğümleri kod çalıştırır. Multi-User'ın sürücü tanımlarını taşıması gibi biz de Python/COFFEE düğümü içermeyen XPresso grafiklerini güvenle taşıyabiliriz. Kod düğümü içeren grafik bugünkü gibi yerel kalır (o grafiğin çıktıları için host'un sonuçları gitmeye devam eder).

C4D Python API'sinde gereken her şey var: `tag.GetNodeMaster()`, `master.GetRoot()`, `GvNode.GetDown/GetNext` (düğüm ağacı), `GetOperatorID()` (düğüm tipi), düğümün description'ı (bizim `params.dump` ile okunabilir), `GetInPorts/GetOutPorts`, `port.GetDestination()` (bağlantılar); yazarken `master.CreateNode`, `AddPort`, `Connect`. Nesne düğümlerinin işaret ettiği nesneler bizim `{"L": uuid}` bağlantılarımızla çözülür. **Bunların hepsi önce `tests/c4d_manual` altında bir probe ile 2023 ve R19'da ölçülmeli** (port kimlikleri, dinamik portların sürümler arası davranışı, `CreateNode` dönüş değerleri).

### 4.2 Sürülen parametre maskesi (C4D'ye özgü, Blender'da gerekmeyen parça)
1.7'de anlatıldığı gibi C4D'de rig sonuçları belgeye yazılıyor. Rig herkes tarafında çalışmaya başladığında, rig'in yazdığı parametreler **kimse tarafından gönderilmemeli ve kilitte geri alınmamalı**; yoksa iki taraf aynı parametre için kavga eder.
- XPresso grafiğinden çıkarılabilir: bir nesne düğümünün **giriş portu** = o nesnenin o parametresi sürülüyor (DescID'si portta yazılı).
- Constraint, IK, Pose Morph slider'ları gibi bilinen ifade etiketleri için tip bazlı kurallar.
- Tracker bu maskeyi kullanır: maskeli parametre farkı ne gönderilir ne geri alınır. Bu, dünkü düzeltmenin (sadece sayaç oynadıysa geri alma) genelleştirilmiş ve kesin hali; dün "kalan olasılık" dediğim durumu (rig kilitli nesneyi gerçekten başka değere götürüyor) da kapatır.
- Kod taşıyan (yerel kalan) bir grafiğin sürdüğü parametreler için şimdiki "host hesaplar, sonuç gider" davranışı korunur; o parametreleri sadece host gönderir.

### 4.3 Multi-User'dan alınacak küçük ama değerli fikirler
1. **Önlemek, geri almaktan iyidir**: Multi-User başkasının nesnesini seçilemez yapıyor. C4D'de seçilemezlik için yerleşik bir nesne bayrağı yok; katman kilidi var ama kullanıcının katman atamasını bozar. Bizim "seçimi düşür + uyar" yolumuz bu yüzden doğru; ek olarak **değişikliği sunucuya hiç göndermemek** zaten yapılıyor. Değişmesi gereken bir şey yok, sadece 4.2 eklenmeli.
2. **Bağımlılık sırası genel bir grafik olarak**: bugün `KIND_ORDER` + özel yeniden bağlama fonksiyonları (`_relink`, `_reload_weights`, `_reload_dependents`) var. XPresso grafikleri, Pose Morph hedefleri, Weight etiketleri ve malzemeler artınca bu fonksiyonlar çoğalacak. Multi-User'ın `resolve_deps` + ters BFS modeli (her serileştirici kendi bağımlılıklarını bildirir, applier sırayı grafikten çıkarır) uzun vadede daha temiz. Acil değil; dördüncü özel yeniden bağlama fonksiyonu gerekirse yapılmalı.
3. **Senkronize edilmeyen tipler için açık raporlama**: ikisinde de yok; bizim `unsynced_report()` zaten daha iyi.

### 4.4 Mixer'dan alınacak fikirler
1. **Ayna ile yankı bastırma**: bizde zaten var (shadow).
2. **Mod bazlı erteleme**: Mixer POSE modunda gönderimi erteliyor. Biz tersine canlılık istiyoruz ve `tween` ile yumuşatıyoruz; almayalım. Ama "nokta düzenleme modunda dev mesh'i her karede gönderme" için bizde yavaş okuma koruması var; yeterli.
3. **Hariç listesi ile hesaplanmış değerleri dışlama** (`PoseBone.matrix` gibi): bizdeki karşılığı `ABS_*/GLOBAL_*` dışlaması. 4.2 bunun dinamik (rig'e göre) hali.

## 5. Pose Morph (yüz) için tasarım notu

Multi-User'ın shape key yaklaşımının C4D karşılığı:

| Blender shape key | C4D Pose Morph |
|---|---|
| `key_blocks[i].name / value / slider_min / slider_max / mute` | morph adı, güç slider'ı (tag description'ında dinamik parametre), sınırlar |
| `key_block.data[*].co` (mutlak koordinat) | `CAMorph` hedefinin nokta verisi (göreli veya mutlak mod) |
| `relative_key` | temel poz (base morph, indeks 0) |
| Shape key animasyonu/sürücüleri | Slider track'leri (bizde zaten taşınıyor) + XPresso (4.1) |

Önerilen veri: `tag.data["pm"] = {"m": [{"n": ad, "pts": {parça: float x3}}...], "mode": ...}`, noktalar 128'lik parçalarla (geometrideki gibi, sadece değişen morph/parça gider). Slider değerleri zaten genel `params.dump` ile okunabiliyor olabilir; **ilk adım bir probe**: 2023 ve R19'da `CAPoseMorphTag` üzerinde `GetMorphCount`, `GetMorph(i)`, `CAMorph.SetMode(doc, tag, CAMORPH_COPY_FLAGS_NONE, CAMORPH_MODE_REL)`, `GetNode(0)` nokta okuma/yazma, `UpdateMorphs` ve sayaç davranışını ölçmek. Yüklemede Multi-User gibi "hepsini sil, baştan kur" basit ve kesin; morph sayısı değişmediyse sadece nokta parçaları yazılır (modelleme kuralımızla aynı).

**Sıra önerisi:**
1. Pose Morph verisi (önceden planlanan iş; tek başına değer üretir: misafir yüz şekillerini alır, slider'lar host'tan gelir).
2. XPresso grafiklerinin taşınması (kod düğümü olmayanlar) + sürülen parametre maskesi. En büyük kazanç bu: misafirde rig ve yüz kendisi çalışır, kilit/rig çatışması kökten kapanır, host'un gönderdiği trafik azalır.
3. (İsteğe bağlı) bağımlılık grafiği refaktörü (4.3/2).

## 6. Kaynak dosya referansları
- replication: `replication/porcelain.py` (add/commit/push/apply/lock), `repository.py` (graf, rbfs), `server.py` (komut/veri/TTL thread'leri, kilit sıfırlama), `objects.py` (pickle serileştirme), `protocol.py` (ReplicatedDatablock arayüzü).
- multi_user: `handlers.py` (sahip kontrolü, yankı listesi), `timers.py` (seçim=kilit, hide_select, ApplyTimer), `bl_types/bl_object.py` (shape key, pose, constraint, delta daraltma), `bl_types/bl_action.py` (sürücü tanımları), `bl_types/bl_armature.py` (EDIT modu).
- mixer: `doc/synchronization.md`, `doc/protocol.md`, `mixer/handlers_generic.py` (POSE modunda erteleme), `blender_data/filter.py` (PoseBone hariç listesi), `blender_data/struct_proxy.py` (özellik farkı), `blender_data/bpy_data_proxy.py` (alıcıda aynayı güncelleme), `broadcaster/apps/server.py` (oda günlüğü ve birleştirme).
