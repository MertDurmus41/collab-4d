# Collab 4D sunucusunu ücretsiz bir bulut sunucusuna kurmak

Sonuç: internette 7/24 açık bir Collab 4D sunucusu. Dünyanın her yerinden herkes Cinema 4D'de
**Host address** olarak bu sunucunun adresini, **Port** ve **Password** yazıp **Join** eder. Kimsenin
modeminde ayar gerekmez, kimse ek program kurmaz.

- **İlk Join eden kişi açık olan sahnesini paylaşır**, sonra gelenler o sahneye girer.
- Herkes ayrılınca oturum sıfırlanır; bir sonraki ilk giren yine kendi sahnesini getirir. Kimsenin işi
  kaybolmaz, herkesin kopyası kendi Cinema 4D'sinde durur.
- **Sınır:** bir sunucuda aynı anda tek oturum var. Şifreyi bilen herkes aynı sahneye girer. Bu yüzden
  şifreyi yalnızca birlikte çalıştığın kişilerle paylaş.
- **Güvenlik:** şifre ağdan gitmez, ama sahne trafiği şifresizdir. Gizli (NDA'lı) işler için uygun değil.

Süre: hesap açmak 10-20 dakika, sunucuyu kurmak 5 dakika.

## 1. Oracle Cloud ücretsiz hesap

1. <https://www.oracle.com/cloud/free/> → **Start for free**.
2. Kart bilgisi istenir. Bu yalnızca doğrulama içindir; "Always Free" kaynaklarda ücret çıkmaz.
3. **Home Region** seçimi sonradan değiştirilemez. Kullanıcıların çoğu Türkiye/Avrupa'daysa
   **Germany Central (Frankfurt)** seç; ABD'den ve Asya'dan gelenler için de makul bir orta nokta.

> Not: Oracle, uzun süre neredeyse hiç kullanılmayan "Always Free" sunucuları geri alabileceğini
> yazıyor. Bizim sunucu çoğu zaman boşta bekler, bu risk bizim için geçerli olabilir. Hesabı
> **Pay As You Go**'ya yükseltmek bu riski kaldırır ve ücretsiz sınırlar içinde yine para çıkmaz
> (bunu Oracle'ın güncel koşullarından kontrol et).

## 2. Sunucuyu (VM) oluştur

Oracle'ın arayüzündeki adlar biraz değişebilir; aşağıdakiler Kasım 2026'ya göre yaklaşık adlardır.

1. Menü → **Compute → Instances → Create instance**.
2. **Image:** Canonical **Ubuntu** (22.04 ya da 24.04).
3. **Shape:** **VM.Standard.E2.1.Micro** (yanında "Always Free eligible" yazar). Ampere A1 da
   ücretsizdir ama çoğu bölgede "kapasite yok" hatası verir.
4. **Networking:** yeni bir VCN ve **public subnet** oluşturulsun; **Assign a public IPv4 address** açık
   olsun.
5. **Add SSH keys → Generate a key pair for me → Save private key.** İnen `.key` dosyasını sakla, onsuz
   sunucuya giremezsin.
6. **Create**. Durum **Running** olunca sayfadaki **Public IP address**'i not et (ör. `203.0.113.10`).

## 3. Portu aç (Oracle'ın kendi güvenlik duvarı)

1. Sunucunun sayfasında subnet bağlantısına tıkla → **Security Lists → Default Security List**.
2. **Add Ingress Rules:**
   - Source CIDR: `0.0.0.0/0`
   - IP Protocol: `TCP`
   - Destination Port Range: `47800`
3. **Add Ingress Rules** ile kaydet.

Sunucunun içindeki güvenlik duvarını kurulum betiği kendisi açar.

## 4. Kur (kendi bilgisayarından, tek komut)

Windows'ta repo klasöründe **Git Bash** aç (macOS/Linux'ta normal terminal) ve çalıştır:

```bash
bash server/deploy/deploy.sh ubuntu@PUBLIC_IP ~/Downloads/ssh-key-XXXX.key
```

`PUBLIC_IP` yerine 2. adımdaki adresi, anahtar yerine indirdiğin dosyanın yolunu yaz. İlk bağlantıda
"Are you sure you want to continue connecting" sorulursa çıkmaz; betik bunu otomatik kabul eder.

Sonunda şöyle bir kutu çıkar:

```
 Collab 4D server is running.
 Port:     47800
 Password: Xy7...(16 karakter)
```

Şifreyi kendin seçmek istersen: `... deploy.sh ubuntu@IP anahtar.key --password BenimSifrem`.
Komutu sonra tekrar çalıştırmak sunucuyu günceller; şifre aynı kalır.

## 5. Cinema 4D'de

- **Host address:** sunucunun Public IP'si
- **Port:** 47800
- **Password:** kurulumun yazdığı şifre
- **Join** (Host session değil!)

Sahnesi olan kişi **ilk** Join eder, diğerleri sonra.

## Sorun giderme

| Belirti | Neden / çözüm |
|---|---|
| `deploy.sh`: "cannot log in" | Adres yanlış, sunucu henüz Running değil ya da anahtar dosyası başka. Kullanıcı adı Ubuntu'da `ubuntu`. |
| Join: "cannot connect ... timed out" | 3. adımdaki 47800 kuralı eksik. |
| Join: "wrong password" | Şifreyi büyük/küçük harfe dikkat ederek yaz. 5 yanlış denemede o IP 60 sn bekletilir. |
| Sunucunun kaydını görmek | `ssh -i anahtar.key ubuntu@IP` sonra `sudo journalctl -u collab4d -f` |
| Sunucuyu yeniden başlatmak | `sudo systemctl restart collab4d` |
| Şifreyi değiştirmek | `deploy.sh ... --password YeniSifre` |

## Diğer seçenekler

- **Google Cloud ücretsiz katmanı (e2-micro):** yalnızca ABD bölgelerinde (Oregon, Iowa, South
  Carolina); Türkiye/Avrupa'dan gecikme daha yüksek olur. Aynı `deploy.sh` çalışır; kullanıcı adı
  Google'da senin hesap adındır.
- **Her Linux sunucusu:** Ubuntu, Debian, Oracle Linux, Python 3.6 ve üstü. `deploy.sh` aynı.
- **Docker:** `server/Dockerfile` ve `server/docker-compose.yml` hâlâ duruyor.
