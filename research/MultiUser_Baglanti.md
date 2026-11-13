# Multi-User (Blender) nasıl bağlanıyor, bizde ne uygulandı

Tarih: 2026-11-11. Kaynaklar: `gitlab.com/slumber/multi-user` @ `74c20b5` ve ağ çekirdeği
`gitlab.com/slumber/replication` @ `a3d36bc` (ikisi de 2026 Eylül, açık kaynak Python, GPL).

## REA hakkında

İstenen tersine mühendislik aracı `morluto/rea` (yerelde `Desktop/C4D_Collab_Add/repos/rea`). Python
tarafında yalnızca derlenmiş `.pyc` başlığını, kod nesnelerini ve PyInstaller paketlerini inceliyor;
kaynağı geri çevirmiyor (`docs/cli.md`, `inspect-python-pyc`). Yerel kopya derlenmemiş (`node_modules` ve
`dist` yok), kurulumu (`npx rea-agents setup`) ise ajan yapılandırmasını değiştiriyor. Multi-User'ın
kaynağı zaten açık olduğu için REA'nın göstereceği her şey kaynakta daha ayrıntılı bulunuyor. Bu yüzden
REA kurulmadı. Aşağıdaki her iddia kaynak dosyalara dayanıyor.

## 1. Bağlantı tam olarak nasıl

| Konu | Multi-User'da | Dosya |
|---|---|---|
| Taşıma | Düz TCP, IPv4 (`AF_INET`). Eskiden ZeroMQ'ydu, artık yalnız standart kütüphane (`socket` + `selectors`) | `replication/net.py` |
| Port | **Tek port** (varsayılan 5555; CLI'da 5560). Her istemci 3 bağlantı açar; bağlantının ilk baytları `kimlik + rol` (`C` komut, `D` veri, `T` TTL/ping) | `net.py` `Listener`, `Dealer._finish_connect` |
| Host olmak | Blender, sunucuyu **ayrı bir Python süreci** olarak başlatır (`subprocess.Popen(... replication.server -p port -spwd ... -apwd ...)`), `0.0.0.0`'a bağlanır, sonra kendi istemcisiyle ona bağlanır | `replication/interface.py` `host()` |
| Katılmak | Kullanıcı panelde **IP + port** yazar (`preferences.py`: `ip` varsayılan `127.0.0.1`, `port` 5555) | `multi_user/preferences.py` |
| Şifre | Sunucu şifresi ve admin şifresi **düz metin** olarak ağdan gider (`"S_PWD": server_password`) ve sunucuda `str(server_password) != self._server_password` ile karşılaştırılır. Şifreler sunucu sürecine komut satırı argümanı olarak da verilir | `interface.py:117-124`, `server.py` `_login_client` |
| Şifreleme | **Yok** (TLS yok) | - |
| NAT aşma, relay, P2P | **Yok.** `net.py` başlığı yalnızca "ileride p2p/QUIC eklenebilir" diyor | `net.py` belge dizgesi |
| Canlılık | Ayrı TTL kanalında ping; zaman aşımında istemci düşürülür | `ttl.py`, `server.py` `ServerTTL` |
| Boş sunucuya ilk giriş | Dedicated server `WAITING` durumunda başlar. İlk gelen admin sahnesini yükler (`handle_server_repository_init`: ilk `INIT` kabul, sunucu `SRV_SYNC`'e geçer, sonraki `INIT`'ler `REJECTED`). Diğerleri lobide bekler, yükleme bitince sahneyi alır | `server.py:304-352`, `multi_user/operators.py` `SessionInitOperator` |

## 2. İnternet üzerinden: Multi-User'ın kendi belgesi ne diyor

`multi-user/docs/hosting_internet/index.rst` üç yol veriyor, hiçbiri eklentinin içinde değil:

1. **ZeroTier** (ücretsiz, önerilen) ya da Hamachi (5 kişiye kadar ücretsiz): herkes uygulamayı kurar,
   aynı sanal ağa katılır, host'un o ağdaki IP'sine bağlanır. Ortadaki sunucuları ZeroTier şirketi
   çalıştırır.
2. **Port yönlendirme**: belge bunu "en güvensiz yol" diye anıyor.
3. **Dedicated server**: Blender'sız sunucu (`replication.server` ya da Docker imajı) bir VPS'te çalışır;
   adım adım **Google Cloud ücretsiz katmanı** rehberi var. Herkes o sunucunun dış IP'sine bağlanır.

Yani Blender tarafında "para yok" izlenimi şuradan geliyor: ya kullanıcı ZeroTier kuruyor, ya modemini
ayarlıyor, ya da ücretsiz bir bulut sunucusu kiralıyor. Eklenti ayrıca bir sunucu işletmiyor.

## 3. Bizimle karşılaştırma

| | Multi-User | Collab 4D (bu değişiklikten önce) |
|---|---|---|
| Taşıma | Düz TCP, tek port, 3 bağlantı | Düz TCP, tek port, 1 bağlantı |
| Şifre | Düz metin ağdan gidiyor | HMAC meydan okuma, şifre ağdan gitmiyor |
| Kaba kuvvet koruması | Yok | IP başına 5 hatada 60 sn yasak, hız sınırı |
| Şifreleme | Yok | Yok |
| Dedicated server | Var, ilk gelen sahnesini yükler | `run_server.py` + Docker var, ama **ilk gelen sahnesini yüklemiyordu**: herkes misafir sayılıyor, sunucu boş kalıyordu |
| İnternet rehberi | ZeroTier, port yönlendirme, Google Cloud | Yok |

Sonuç: Multi-User'ın ücretsiz bulut yolunun bizde çalışmaması için tek bir eksik vardı, o da
"boş sunucuya ilk gelen sahnesini paylaşır" kuralı. Geri kalan her şey bizde zaten var ya da daha güvenli.

## 4. Bizde uygulanan

- `core/server.py` `SessionServer._claim_seed`: sahne boşken (`scene.seq == 0`) ilk katılan kişiye
  `WELCOME` içinde `seed=True` verilir. Başka bir bağlı kişi bu hakkı tutuyorsa ya da sahnede içerik
  varsa `False`. Paylaşan kişi hiçbir şey göndermeden ayrılırsa hak sıradakine geçer. Kural yalnızca
  `first_joiner_seeds=True` ile açılır; bunu yalnız `run_server.py` (ve Docker imajı) yapar. Host'un kendi
  Cinema 4D'sindeki sunucuda kapalıdır, çünkü testler bir yarış buldu: host'un kendi istemcisinden önce
  bir misafirin girişi işlenirse paylaşma hakkını misafir alabiliyordu.
- `core/session.py`: `seeds_scene` (sunucu "sahneni paylaş" dedi) ve `leads` (`is_host` ya da
  `seeds_scene`).
- `c4d_adapter/sync.py`: sahneyi paylaşma ve "misafir host'un karesine geçer" kuralı `is_host` yerine
  `leads`'e bakar. Paylaşan kişinin oturumdan ayrılması oturumu bitirmez (sunucu onda değil).
- `c4d_adapter/ui_dialog.py`: durum satırında "(you shared your scene)".
- `server/run_server.py`: açılışta kuralı yazar. README'de ücretsiz yollar anlatıldı.
- Uyumluluk: `WELCOME`'a yalnızca bir alan eklendi, `PROTOCOL_VERSION` 1 olarak kaldı. Eski
  istemciler alanı yok sayar (eski davranış); eski sunucular alanı göndermez (eski davranış).
- Testler: `tests/core/test_controller.py` (kural ve devir), `tests/adapter/test_sync.py`
  `DedicatedServerTest` (iki sahte Cinema 4D, sunucu üzerinden sahne ve düzenleme, kare).
- Gerçek Cinema 4D: `tests/c4d_real/dedicated_server_check.py` gerçek `run_server.py` sürecini başlatır, c4dpy
  içinde üç tam istemci çalıştırır. 2026-11-11, Cinema 4D 2023.1, 744 nesnelik rig sahnesi (Fight_test11):
  **13/13 geçti**. İlk katılan sahnesini paylaştı, ikinci kişi 744 nesnenin hepsini aldı, ikincinin düzenlemesi
  ilkine ulaştı, sonradan katılan da misafir oldu ve düzenlemeyi gördü. R19 ile ve gerçek bir bulut sunucusunda
  denenmedi.

## 5. Bizde uygulanmayanlar ve nedenleri

- **Düz metin şifre, komut satırında şifre**: bizim HMAC yöntemimiz daha güvenli, geri adım atılmadı.
- **3 ayrı bağlantı (komut / veri / TTL)**: bizim tek bağlantı + ping zaten aynı işi görüyor. Büyük bir
  sahne yüklenirken pingin gecikmesi riski bizde 20 sn'lik zaman aşımıyla karşılanıyor.
- **Lobi ekranı ve "boş başlat" seçeneği**: ilk sürümde kural otomatik. İstenirse eklenebilir.
- **ZeroTier/Tailscale entegrasyonu**: eklentiye gömülecek bir şey değil, README'de rehber olarak
  anlatıldı.

## 6. Ücretsiz internet yolları (bugün çalışır)

1. Herkes **Tailscale** ya da **ZeroTier** kurar ve aynı ağa katılır. Host "Host session"a basar,
   diğerleri host'un o ağdaki adresine (Tailscale'de `100.x.y.z`) Join eder. Not: Windows Güvenlik
   Duvarı sorusunda yalnızca "Özel ağlar" seçildiyse ve sanal ağ adaptörü "Ortak" sayılıyorsa bağlantı
   engellenebilir. Bu denenmedi.
2. Ücretsiz bir bulut sunucusunda `docker compose -f server/docker-compose.yml up -d` çalıştırılır ve
   bulut güvenlik duvarında 47800/TCP açılır. Sahnesi olan kişi **ilk** Join eder, diğerleri sonra.
   Kimsenin modeminde ayar gerekmez. Trafik şifresiz olduğu için güvenilir bir şifre seçilmeli.
