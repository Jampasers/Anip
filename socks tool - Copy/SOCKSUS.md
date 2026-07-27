# Toko SOCKS5 Private (`!socksus`)

Buyer beli 1 socks → dapat `nifstore.duckdns.org:1080` dengan **username & password
sendiri**. Relay di mesin ini memakai kredensial itu untuk menentukan **IP pool mana**
yang dipakai buyer tersebut. Satu buyer = satu IP pool, terkunci (lock).

```
buyer A --nifstore.duckdns.org:1080:nifa1b2c3:xxxx--> RELAY --> pool #12 (1.2.3.4:1080)
buyer B --nifstore.duckdns.org:1080:nif9f8e7d:yyyy--> RELAY --> pool #13 (5.6.7.8:1080)
```

Yang dilihat buyer selalu `nifstore.duckdns.org:1080` — IP pool di belakangnya tidak
pernah bocor ke buyer.

## Menyalakan

**1. Jalankan relay dalam mode multi-user** (di mesin yang domainnya diarahkan
DuckDNS):

```powershell
powershell -ExecutionPolicy Bypass -File run_relay.ps1 -MultiUser
```

Itu membuka firewall, update DuckDNS, lalu menjalankan relay yang membaca akun dari
`..\discord_sqlite_bot.db`. Kalau database-nya di tempat lain: `-Db "D:\path\bot.db"`.

Tanpa `-MultiUser`, perilakunya masih seperti dulu (satu user/pass, satu upstream).

**2. Jalankan bot** seperti biasa (`python bot_core.py`). Modul `cmd_socksus`
terpasang otomatis dan membuat tabelnya sendiri.

**3. Isi IP pool** — kirim `!pool` di Discord dengan **melampirkan file `.txt`**:

```
1.2.3.4:1080:user1:pass1
5.6.7.8:1080:user2:pass2
9.9.9.9:1080
user:pass@11.22.33.44:1080
```

Format `host:port`, `host:port:user:pass`, dan `user:pass@host:port` semuanya
diterima, dengan atau tanpa awalan `socks5://`. Baris kosong dan yang diawali `#`
dilewati; duplikat otomatis di-skip.

**4. Pasang panel** — ketik `!socksus` di channel yang diinginkan. Cukup **sekali**:
lokasinya disimpan di database, jadi setelah bot restart panel muncul lagi sendiri
(bahkan dipasang ulang otomatis kalau pesannya terhapus). Panel menyegarkan diri
tiap **5 menit**.

## Command admin

| Command | Fungsi |
|---|---|
| `!socksus` | pasang / pindahkan panel ke channel ini |
| `!pool` + lampiran `.txt` | import IP pool |
| `!poollist` | lihat semua IP pool + user socks yang terhubung ke tiap IP |
| `!poollist used` / `free` / `dead` | saring menurut status |
| `!pooldel <id\|host>` | hapus IP dari pool (ditolak kalau masih dipakai buyer) |
| `!pooldead <id>` | tandai IP mati; buyer-nya tinggal klik Change IP untuk pengganti |
| `!poolrevive <id>` | kembalikan IP `dead` ke stok |
| `!hargasocksus <wl>` | ubah harga (default `100` WL = 1 DL) |
| `!socksquota <id_akun> <n>` | set ulang jatah Change IP |

Tiap akun otomatis terkunci ke satu IP pool sejak dibeli dan hanya berpindah lewat
tombol **Change IP** — tidak ada lagi kunci/buka manual.

Contoh keluaran `!poollist`:

```
#12   ASSIGNED  1.2.3.4:1080:user1:pass1
      -> user  : nifstore.duckdns.org:1080:nifa1b2c3:Xk29fJ2mQp01
      -> buyer : 698127357990404157 | akun #4
      -> change: 1/5 | exp 2026-08-26 10:12:03 | ONLINE (3 koneksi)
#13   AVAILABLE 5.6.7.8:1080:user2:pass2
```

Status `ONLINE` datang dari relay: relay menulis tabel `socksus_live` tiap 15 detik
selama ada koneksi hidup. IP pool asli (`1.2.3.4:1080` di atas) hanya terlihat admin
di sini — buyer tidak pernah melihatnya; di DM / My SOCKS / menu Change IP hanya
tertulis `Exit IP: Dedicated (locked)`.

## Tombol panel (buyer)

**Buy SOCKS5** · **Change IP** · **My SOCKS** · Depo QRIS · Deposit WL · Set GrowID ·
My Balance — susunannya sama seperti panel `!socks` yang lama.

**Change IP** gratis **5x**. Yang berpindah cuma IP pool di belakangnya; username,
password, host, dan port buyer **tidak berubah**, jadi buyer tidak perlu setting
ulang apa pun. IP baru berlaku dalam ±10 detik (relay membaca ulang daftar akun),
tanpa restart relay. Koneksi yang sudah terlanjur terbuka tetap lewat IP lama sampai
diputus — suruh buyer disconnect dulu.

## Pengaturan (opsional, di `.env`)

| Variabel | Default | Arti |
|---|---|---|
| `SOCKSUS_HOST` | `nifstore.duckdns.org` | host yang diberikan ke buyer |
| `SOCKSUS_PORT` | `1080` | port relay |
| `SOCKSUS_PRICE_WL` | `100` | harga awal (1 DL); setelahnya pakai `!hargasocksus` |
| `SOCKSUS_DURATION_DAYS` | `30` | masa aktif |
| `SOCKSUS_CHANGE_QUOTA` | `5` | jatah Change IP gratis |
| `SOCKSUS_CHANGE_COOLDOWN_SECONDS` | `60` | jeda antar Change IP |
| `SOCKSUS_PANEL_UPDATE_MINUTES` | `5` | jarak refresh panel |
| `SOCKSUS_CHANNEL_ID` | – | kalau diisi, panel terpasang otomatis di channel ini |
| `SOCKSUS_TITLE` | `Socks5 Private 🌐` | judul produk |

## Yang perlu diingat

- **Relay harus hidup.** Kalau relay mati, semua buyer ikut mati — kredensial mereka
  tidak berarti apa-apa tanpa relay yang menerjemahkannya ke IP pool.
- **Port 1080 TCP dan UDP harus di-forward** ke mesin ini kalau ada NAT. Sama seperti
  catatan di `DUCKDNS.md`; DuckDNS cuma menerjemahkan nama jadi IP.
- **Satu IP pool = satu buyer.** Stok yang tampil di panel adalah jumlah IP pool yang
  belum terpakai. Kalau habis, buyer tidak bisa beli dan Change IP juga gagal —
  tambah pool dengan `!pool`.
- **Jangan hapus IP dari pool yang sedang dipakai.** Pakai `!pooldead` supaya buyer
  bisa pindah sendiri lewat Change IP.
- Akun yang lewat masa aktif otomatis dinonaktifkan dan IP-nya kembali ke stok saat
  panel menyegarkan diri (tiap 5 menit) atau saat `!poollist` dipanggil.
