[English](../README.md) | **Türkçe**

# Collab 4D

Cinema 4D için ücretsiz, açık kaynak (MIT) **canlı ortak çalışma** eklentisi. Birden fazla kişi aynı sahnede aynı anda animasyon ve modelleme yapabilir.

> Durum: **Erken geliştirme aşamasında.** Çalışıyor ama henüz kararlı bir sürüm değil.

- Mimari: [ARCHITECTURE.md](../ARCHITECTURE.md)
- **Farklı evlerden ve ülkelerden birlikte çalışmak (Tailscale, ücretsiz):** [docs/internet.md](internet.md)
- Hedef sürümler: Cinema 4D R17 (2016) ve üstü (R17-R22: Python 2.7; R23: 3.7; 2023: 3.9; 2024 ve sonrası: 3.11+)

| Sürüm | Durum |
|---|---|
| R19, 2023 | **gerçek testle doğrulandı** |
| R17, R18, R20, R21, R22, R23, 2024, 2025, 2026 | API uyumu belgelerden doğrulandı, yedek yollar sahte sürüm profilleriyle test edildi; **gerçek sürümde denenmedi** |

Ayrıntı ve sınırlar: [ARCHITECTURE.md](../ARCHITECTURE.md) bölüm 7g. Bir sürümü açabiliyorsanız [tests/c4d_manual/CHECKLIST.md](../tests/c4d_manual/CHECKLIST.md) ve `spike_probe.py` sonuçları çok işe yarar.

## Geliştiriciler için

Testler (C4D gerektirmez, Python 2.7 veya 3.x):

```bash
py -m unittest discover -s tests/core -v
```

```bash
py -m unittest discover -s tests/adapter -v
```

Sürüm uyumluluğu (API envanteri, sürüm profilleri, Python 2.7/3.7 denetimi):

```bash
py -m unittest discover -s tests/compat -v
```

```bash
py -m unittest discover -s tests/adapter -p test_profiles.py -v
```

Gerçek C4D'de elle test: [tests/c4d_manual/CHECKLIST.md](../tests/c4d_manual/CHECKLIST.md)

Bağımsız sunucu:

```bash
py server/run_server.py --port 47800 --password gizli
```

Bu sunucu Cinema 4D'siz çalışır (Docker: [server/Dockerfile](../server/Dockerfile)). Kimse host olmadığı için **ilk katılan kişi açık
sahnesini paylaşır**, sonra katılanlar o sahneye misafir olarak girer (Blender'daki Multi-User'ın dedicated
server'ı da böyle çalışır). Ülkeler arası kullanım için bu sunucu ücretsiz bir bulut sunucusunda (Google Cloud ya da
Oracle'ın ücretsiz katmanı) çalıştırılabilir; herkes onun IP adresine ve portuna Join eder, kimsenin modeminde
ayar gerekmez. Diğer ücretsiz yol: herkes ZeroTier ya da Tailscale kurar, host olan kişinin o ağdaki adresine
Join edilir. Ayrıntılar: [research/MultiUser_Baglanti.md](../research/MultiUser_Baglanti.md).

**Ücretsiz bulut sunucusuna tek komutla kurulum:** [server/deploy/README.md](../server/deploy/README.md).
