# Farklı evlerden birlikte çalışmak (Tailscale) · Working together from different homes

[Türkçe](#türkçe) · [English](#english)

## Türkçe

Collab 4D aynı ev ya da ofis ağındaki bilgisayarları doğrudan bağlar. Farklı evlerde, şehirlerde ya da
ülkelerdeyseniz modemler araya girer. En kolay ücretsiz çözüm **Tailscale**: her bilgisayar bir kere
kurar, sonra Collab 4D bugünkü gibi çalışır. Trafik Tailscale tarafından şifrelenir, modemde ayar
gerekmez.

Menü ve düğme adları Tailscale sürümüne göre biraz değişebilir.

### Host (oturumu açan kişi)

1. <https://tailscale.com/download> adresinden Tailscale'i kur ve aç. Google, Microsoft, GitHub ya da
   Apple hesabınla giriş yap.
2. Arkadaşlarını davet et. İki yol var:
   - **Sadece bu bilgisayarı paylaş (önerilen):** <https://login.tailscale.com/admin/machines>
     sayfasında bilgisayarının satırındaki **…** menüsü → **Share** → bağlantıyı kopyalayıp arkadaşına
     gönder. Arkadaşın yalnızca senin bu bilgisayarını görür.
   - **Ağa üye olarak davet et:** **Users → Invite users**. Ücretsiz planın kişi sınırı vardır; güncel
     sınırı Tailscale'in fiyat sayfasından kontrol et.
3. Cinema 4D'de Collab 4D panelini aç, **Your name** ve **Password** (en az 4 karakter) yaz,
   **Host session**'a bas.
4. Panelde şöyle bir satır çıkar:
   `Others join with port 47800 at:  Tailscale 100.101.102.103 (any home)  192.168.1.5 (same network)`.
   Arkadaşlarına **Tailscale** adresini, portu ve şifreyi gönder.
5. **Windows Güvenlik Duvarı** sorarsa **hem Özel hem Ortak ağlara** izin ver. Windows, Tailscale ağını
   "Ortak" sayabilir. Daha önce yalnız "Özel"i seçtiysen: **Windows Güvenliği → Güvenlik duvarı ve ağ
   koruması → Uygulamanın güvenlik duvarından geçmesine izin ver → Cinema 4D** satırında **Ortak**'ı da
   işaretle.

### Katılanlar

1. Tailscale'i kur, kendi hesabınla giriş yap, host'un gönderdiği bağlantıyı aç.
2. Collab 4D panelinde **Host address** = host'un Tailscale adresi (`100.` ile başlar), **Port**,
   **Password** yaz → **Join**.

### Bilmen gerekenler

- Oturum host'un Cinema 4D'sinde çalışır: host Cinema 4D'yi kapatırsa ya da **Leave** derse oturum
  herkes için biter.
- Host'un bilgisayarında Tailscale açık olmalı. Panelde "Tailscale ..." görünmüyorsa Tailscale kapalıdır
  ya da giriş yapılmamıştır. Panel adresleri dakikada bir yeniler.
- Her şey ücretsizdir; ödeme yalnızca Tailscale'in büyük takım planlarında var.

### ZeroTier (alternatif)

1. Host <https://my.zerotier.com> adresinde ücretsiz hesap açar, **Create A Network** der, **Network ID**'yi
   kopyalar.
2. Herkes <https://www.zerotier.com/download/> adresinden ZeroTier'i kurar, simgeye sağ tıklar →
   **Join New Network** → Network ID.
3. Host, my.zerotier.com'daki ağ sayfasında yeni üyelerin yanındaki **Auth** kutusunu işaretler.
4. Host'un ZeroTier adresi aynı sayfada, host'un satırında yazar. Panel bu adresi "(same network)"
   olarak gösterir. Katılanlar Host address olarak bunu yazar. Güvenlik duvarı adımı Tailscale ile aynı.

## English

Collab 4D connects computers on the same home or office network directly. From different homes or
countries the routers are in the way. The easiest free fix is **Tailscale**: every computer installs it
once, then Collab 4D works as it does at home. Tailscale encrypts the traffic; no router settings.

**Host**

1. Install Tailscale from <https://tailscale.com/download> and sign in.
2. Invite your friends: on <https://login.tailscale.com/admin/machines> open **…** on your computer →
   **Share**, and send the link (they only see that computer). Or **Users → Invite users** (the free plan
   has a user limit; check Tailscale's pricing page).
3. In Cinema 4D open the Collab 4D panel, enter your name and a password (4+ characters), press
   **Host session**.
4. The panel shows `Tailscale 100.x.y.z (any home)`: send that address, the port and the password.
5. If the Windows Firewall asks, allow **both Private and Public** networks: Windows may count the
   Tailscale network as Public.

**Everyone else**

1. Install Tailscale, sign in, open the host's link.
2. In the Collab 4D panel: Host address = the host's Tailscale address (starts with `100.`), Port,
   Password → **Join**.

The session runs inside the host's Cinema 4D: it ends when the host leaves or closes Cinema 4D.
ZeroTier works the same way (create a network on my.zerotier.com, everyone joins it, the host authorizes
them; the panel lists the host's ZeroTier address under "same network").
