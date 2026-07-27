# DuckDNS - nifstore.duckdns.org

Supaya klien konek pakai nama domain, bukan IP yang berubah-ubah.

## Pasang token (sekali saja)

1. Buka <https://www.duckdns.org>, login, salin token di bagian atas halaman.
2. Buka `duckdns.json` di folder ini, ganti `token`-nya:

```json
{
  "domain": "nifstore",
  "token": "token-punyamu-disini",
  "ip_mode": "auto",
  "interval_minutes": 5
}
```

| Field | Arti |
|---|---|
| `domain` | label saja, **tanpa** `.duckdns.org` |
| `token` | token dari dashboard DuckDNS |
| `ip_mode` | `auto` = IP publik yang dilihat DuckDNS (default) · `lan` = IP LAN mesin ini, untuk klien satu jaringan · `1.2.3.4` = alamat tetap, dipakai apa adanya |
| `interval_minutes` | jarak auto-update yang dipasang oleh `-Install` |

Kalau lebih suka tidak menyimpan token di file, set environment variable
`DUCKDNS_TOKEN` — itu menang atas isi file. Flag di command line
(`-Domain`, `-Token`, `-Ip`, `-Lan`, `-IntervalMinutes`) menang atas keduanya.

## Pakai

```powershell
# update sekali sekarang
powershell -ExecutionPolicy Bypass -File duckdns.ps1

# lihat kondisi: DNS sekarang, IP publik, IP LAN, status auto-update
powershell -ExecutionPolicy Bypass -File duckdns.ps1 -Status

# pasang auto-update: jalan saat boot + tiap 5 menit (minta hak Administrator)
powershell -ExecutionPolicy Bypass -File duckdns.ps1 -Install
powershell -ExecutionPolicy Bypass -File duckdns.ps1 -Uninstall

# arahkan domain ke IP LAN, bukan IP publik (klien satu jaringan)
powershell -ExecutionPolicy Bypass -File duckdns.ps1 -Lan
```

Sekalian saat menjalankan relay:

```powershell
powershell -ExecutionPolicy Bypass -File run_relay.ps1 -DuckDns
```

Mode ini mengarahkan domain ke alamat yang **sama** dengan yang diiklankan relay untuk
UDP (`--udp-ip`), lalu mencetak `nifstore.duckdns.org:1080:hanif:hanif` sebagai data
koneksi klien. Kalau update-nya gagal, relay tetap jalan dan mencetak IP seperti biasa.

Log ada di `duckdns.log` (dipangkas otomatis kalau lewat 256 KB).

## Yang perlu diingat

- **DuckDNS cuma menerjemahkan nama jadi IP.** Dia tidak membuka port dan tidak
  menembus NAT. Mesin ini sekarang ber-NAT (LAN `192.168.100.115`, publik terpisah),
  jadi TCP **dan** UDP port 1080 tetap harus di-forward di router ke mesin ini.
  Tanpa itu, domainnya resolve tapi koneksi tetap gagal.
- **`--udp-ip` tetap harus IP, bukan domain.** Alamat itu dikirim mentah ke klien di
  balasan UDP ASSOCIATE; banyak klien tidak menangani tipe alamat domain. Skrip sudah
  mengurus ini — domain hanya untuk alamat yang diketik user.
- **IP publik dari ISP rumah bisa berganti kapan saja.** Itulah gunanya `-Install`:
  tanpa auto-update, domainnya jadi basah begitu IP berubah.
- Task dipasang berjalan sebagai `SYSTEM`, jadi tetap update walau belum ada yang login.
- Kalau ISP memakai CGNAT (IP publik di router beda dengan yang dilihat DuckDNS),
  port forwarding tidak akan bisa dipakai sama sekali — perlu VPS/tunnel, bukan DuckDNS.
