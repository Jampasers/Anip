#!/usr/bin/env python3
"""
socks5_relay.py - Server SOCKS5 lokal yang meneruskan (chain) ke upstream SOCKS5.

Alur:
    client  --SOCKS5 (user/pass)-->  RELAY (komputer ini:1080)  --SOCKS5-->  upstream
                                                                             217.181.66.23:1339

  * Listen di IP komputer yang dideteksi otomatis, port 1080 (bisa dioverride).
  * Klien wajib auth username/password (default: hanif / hanif).
  * Semua koneksi keluar diteruskan ke upstream SOCKS5 dengan kredensialnya.
  * CONNECT (TCP) dan UDP ASSOCIATE (dipakai game) didukung. BIND ditolak.

MODE MULTI-USER (--db): satu port melayani banyak buyer sekaligus. Username/password
klien dicocokkan ke tabel `socksus_account` di database bot Discord, dan tiap akun
punya upstream sendiri dari `socksus_pool`:

    buyer A --...:1080:nifa1b2c3:xxx--> RELAY --> pool #12 (1.2.3.4:1080)
    buyer B --...:1080:nif9f8e7d:yyy--> RELAY --> pool #13 (5.6.7.8:1080)

Daftar akun dibaca ulang berkala (--db-reload), jadi pembelian dan Change IP dari
bot langsung berlaku tanpa restart relay. Relay juga menulis tabel `socksus_live`
supaya `!poollist` di bot bisa menampilkan siapa yang sedang tersambung.
Tanpa --db, perilakunya persis seperti dulu: satu user/pass, satu upstream.

UDP butuh upstream yang juga mendukung UDP ASSOCIATE. Port UDP-nya sama dengan port
TCP (1080), karena banyak klien game mengabaikan BND.PORT dari balasan UDP ASSOCIATE
dan tetap mengirim datagram ke port proxy-nya. Jadi yang perlu dibuka/di-forward
cuma satu nomor port, untuk TCP dan UDP sekaligus.
Di mesin ber-NAT, alamat yang diiklankan ke klien wajib alamat yang bisa dijangkau
klien: pakai --udp-ip.

Tiap 5 detik dicetak satu baris ringkasan: kecepatan turun/naik dalam Mbps, jumlah
koneksi yang sedang aktif ke pool upstream, dan total bandwidth terpakai sejak start.
Baris itu bertambah ke bawah (tidak menimpa baris sebelumnya) dan ikut masuk ke
relay.log, yang sekarang menumpuk antar-sesi dan dipotong otomatis di 5 MB.
Log per koneksi disembunyikan supaya tidak menenggelamkan ringkasan - munculkan
lagi dengan -v kalau sedang mendiagnosis sesuatu.

Contoh:
    python socks5_relay.py
    python socks5_relay.py --user andi --pass rahasia --port 1080
    python socks5_relay.py --listen 0.0.0.0 --up 1.2.3.4:1080:userup:passup
    python socks5_relay.py --udp-ip 192.168.100.115
    python socks5_relay.py --db ../discord_sqlite_bot.db   # multi-user dari bot
    python socks5_relay.py --stats 10        # ringkasan tiap 10 detik
    python socks5_relay.py --stats 0 -v      # matikan ringkasan, log per koneksi

Klien lalu memakai:  socks5://user:pass@<IP komputer>:1080
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import socket
import sqlite3
import struct
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

BUF = 64 * 1024

# ---- default upstream (dari permintaan: host:port:user:pass) ----------------
UP_HOST = "217.181.72.84"
UP_PORT = 1339
UP_USER = "IPv4D_mSDwx2BPSn"
UP_PASS = "FKfISfEF9IcuGHi"

# ---- default kredensial klien (yang dipakai user untuk konek ke RDP ini) -----
CLIENT_USER = "hanif"
CLIENT_PASS = "hanif"

log = logging.getLogger("socks5relay")


class _Stats:
    """Penghitung lalu lintas untuk baris ringkasan tiap beberapa detik.

    Semua penambahan terjadi di dalam event loop yang sama (asyncio single-thread),
    jadi tidak perlu lock. Angka byte dihitung setelah data benar-benar dibaca dari
    satu sisi, sebelum ditulis ke sisi lain.
    """

    def __init__(self) -> None:
        self.up_bytes = 0     # klien -> upstream
        self.down_bytes = 0   # upstream -> klien
        self.tcp_active = 0   # tunnel TCP yang sedang hidup
        self.tcp_total = 0
        self.udp_active = 0   # asosiasi UDP yang sedang hidup
        self.udp_total = 0


stats = _Stats()


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


async def report_stats(interval: float) -> None:
    """Cetak satu baris ringkasan tiap `interval` detik, terus bertambah ke bawah.

    Sengaja memakai log.info biasa (bukan '\\r' yang menimpa baris) supaya riwayatnya
    tetap terbaca di terminal maupun di relay.log.
    """
    prev_up = prev_down = 0
    prev_t = time.monotonic()
    while True:
        await asyncio.sleep(interval)
        now = time.monotonic()
        dt = max(now - prev_t, 1e-9)
        d_up = stats.up_bytes - prev_up
        d_down = stats.down_bytes - prev_down
        prev_t, prev_up, prev_down = now, stats.up_bytes, stats.down_bytes
        log.info(
            "STAT  turun %6.2f Mbps | naik %6.2f Mbps | pool aktif: %d TCP, %d UDP | "
            "terpakai %s (turun %s / naik %s)",
            d_down * 8 / dt / 1_000_000, d_up * 8 / dt / 1_000_000,
            stats.tcp_active, stats.udp_active,
            human_bytes(stats.up_bytes + stats.down_bytes),
            human_bytes(stats.down_bytes), human_bytes(stats.up_bytes),
        )

# Kode balasan SOCKS5 (RFC 1928)
REP_OK = 0x00
REP_GENERAL_FAIL = 0x01
REP_NOT_ALLOWED = 0x02
REP_NET_UNREACH = 0x03
REP_HOST_UNREACH = 0x04
REP_CONN_REFUSED = 0x05
REP_CMD_NOT_SUPPORTED = 0x07
REP_ATYP_NOT_SUPPORTED = 0x08


def detect_ip() -> str:
    """IP interface lokal komputer ini (di VPS/RDP biasanya IP privat, mis. 10.x)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # tidak mengirim apa pun untuk UDP
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def detect_public_ip() -> Optional[str]:
    """IP publik (yang dipakai user dari internet), via layanan cek-IP. None kalau gagal."""
    import urllib.request

    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com",
                "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                ip = resp.read().decode().strip()
                if ip and all(part.isdigit() for part in ip.split(".")):
                    return ip
        except Exception:
            continue
    return None


class Upstream:
    def __init__(self, host: str, port: int, user: str, password: str):
        self.host, self.port, self.user, self.password = host, port, user, password

    @classmethod
    def parse(cls, spec: str) -> "Upstream":
        """'host:port:user:pass' (user:pass opsional)."""
        parts = spec.split(":")
        if len(parts) not in (2, 4):
            raise ValueError("format --up: host:port atau host:port:user:pass")
        host, port = parts[0], parts[1]
        if not port.isdigit():
            raise ValueError("port upstream harus angka")
        user, password = (parts[2], parts[3]) if len(parts) == 4 else ("", "")
        return cls(host, int(port), user, password)

    @property
    def key(self) -> Tuple[str, int]:
        """Identitas upstream, dipakai untuk cache 'menerima nama domain atau tidak'."""
        return (self.host, self.port)

    def __str__(self) -> str:
        return f"{self.host}:{self.port}"


class Account:
    """Satu tenant: kredensial yang dipakai klien + upstream miliknya sendiri.

    `counter` sengaja dipegang bersama AccountStore (list, bukan angka) supaya
    hitungannya tidak hilang tiap kali daftar akun dibaca ulang dari database.
    """

    __slots__ = ("id", "user", "password", "up", "counter")

    # indeks di dalam counter
    TCP, UDP, UP_BYTES, DOWN_BYTES, DIRTY = 0, 1, 2, 3, 4

    def __init__(self, account_id: Optional[int], user: bytes, password: bytes,
                 up: Upstream, counter: List[int]):
        self.id = account_id
        self.user = user
        self.password = password
        self.up = up
        self.counter = counter

    @property
    def label(self) -> str:
        name = self.user.decode("ascii", errors="replace")
        return f"{name}#{self.id}" if self.id else name

    def add_up(self, n: int) -> None:
        self.counter[Account.UP_BYTES] += n
        self.counter[Account.DIRTY] = 1

    def add_down(self, n: int) -> None:
        self.counter[Account.DOWN_BYTES] += n
        self.counter[Account.DIRTY] = 1


class AccountStore:
    """Sumber daftar akun: statis (--user/--pass/--up) atau dari database bot.

    Mode database membaca `socksus_account` + `socksus_pool` tiap `reload_every`
    detik. Query-nya ringan (puluhan baris) dan hasilnya di-cache, jadi tidak ada
    akses disk di jalur per-koneksi.
    """

    def __init__(self, db_path: Optional[str], static: Optional[Account] = None,
                 reload_every: float = 10.0):
        self.db_path = db_path
        self.reload_every = reload_every
        self._static = static
        self._by_user: Dict[bytes, Account] = {}
        self._counters: Dict[int, List[int]] = {}
        self._loaded_at = 0.0
        self._conn: Optional[sqlite3.Connection] = None
        # reload() dipanggil dari event loop, flush_live() dari thread executor,
        # jadi koneksinya dipakai lintas-thread dan wajib dikunci sendiri.
        self._db_lock = threading.Lock()
        if static is not None:
            self._by_user[static.user] = static

    # ------------------------------------------------------------- database #
    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            # timeout: bot Discord memakai file yang sama, jadi tulisan bisa antre.
            self._conn = sqlite3.connect(
                self.db_path, timeout=10.0, check_same_thread=False
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS socksus_live (
                    account_id INTEGER PRIMARY KEY,
                    active_tcp INTEGER NOT NULL DEFAULT 0,
                    active_udp INTEGER NOT NULL DEFAULT 0,
                    up_bytes INTEGER NOT NULL DEFAULT 0,
                    down_bytes INTEGER NOT NULL DEFAULT 0,
                    last_seen_at TEXT
                )
                """
            )
            self._conn.commit()
        return self._conn

    def _counter_for(self, account_id: int) -> List[int]:
        counter = self._counters.get(account_id)
        if counter is None:
            counter = [0, 0, 0, 0, 0]
            self._counters[account_id] = counter
        return counter

    def reload(self, force: bool = False) -> None:
        if not self.db_path:
            return
        now = time.monotonic()
        if not force and now - self._loaded_at < self.reload_every:
            return
        self._loaded_at = now
        try:
            with self._db_lock:
                rows = self._connect().execute(
                    """
                    SELECT a.id, a.client_user, a.client_pass,
                           p.host, p.port, p.username, p.password
                    FROM socksus_account a
                    JOIN socksus_pool p ON p.id = a.pool_id
                    WHERE a.status = 'active'
                      AND p.status = 'assigned'
                      AND (a.expires_at IS NULL
                           OR a.expires_at > strftime('%Y-%m-%d %H:%M:%S','now'))
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            # Database sibuk/terkunci: pertahankan daftar lama, jangan putus layanan.
            log.warning("gagal membaca akun dari database: %s", exc)
            return

        fresh: Dict[bytes, Account] = {}
        if self._static is not None:
            fresh[self._static.user] = self._static
        for account_id, user, password, host, port, up_user, up_pass in rows:
            if not user or not password or not host:
                continue
            key = str(user).encode()
            fresh[key] = Account(
                int(account_id), key, str(password).encode(),
                Upstream(str(host), int(port), str(up_user or ""), str(up_pass or "")),
                self._counter_for(int(account_id)),
            )

        added = set(fresh) - set(self._by_user)
        removed = set(self._by_user) - set(fresh)
        changed = [
            u for u in set(fresh) & set(self._by_user)
            if str(fresh[u].up) != str(self._by_user[u].up)
        ]
        self._by_user = fresh
        for user in changed:
            log.info("akun %s pindah upstream -> %s",
                     user.decode("ascii", errors="replace"), fresh[user].up)
        if added or removed:
            log.info("daftar akun diperbarui: %d aktif (+%d / -%d)",
                     len(fresh), len(added), len(removed))
        # Akun yang hilang tidak boleh terus dilaporkan online di !poollist.
        self._prune_counters()

    def _prune_counters(self) -> None:
        live_ids = {a.id for a in self._by_user.values() if a.id}
        for account_id in [i for i in self._counters if i not in live_ids]:
            # Koneksi yang masih hidup tetap dibiarkan menghitung sampai putus.
            counter = self._counters[account_id]
            if counter[Account.TCP] <= 0 and counter[Account.UDP] <= 0:
                self._counters.pop(account_id, None)

    # --------------------------------------------------------------- lookup #
    def lookup(self, user: bytes, password: bytes) -> Optional[Account]:
        self.reload()
        account = self._by_user.get(user)
        if account is None or account.password != password:
            return None
        return account

    def active_accounts(self) -> int:
        return len(self._by_user)

    # ------------------------------------------------------------ heartbeat #
    def flush_live(self) -> None:
        """Tulis status per akun supaya bot bisa menampilkan ONLINE/idle.

        Hanya baris yang benar-benar bergerak (ada koneksi hidup atau byte
        bertambah) yang di-update, jadi `last_seen_at` akun yang menganggur
        membasi sendiri dan bot menandainya idle.
        """
        if not self.db_path:
            return
        rows = []
        for account in self._by_user.values():
            if account.id is None:
                continue
            counter = account.counter
            busy = counter[Account.TCP] > 0 or counter[Account.UDP] > 0
            if not busy and not counter[Account.DIRTY]:
                continue
            counter[Account.DIRTY] = 0
            rows.append((account.id, counter[Account.TCP], counter[Account.UDP],
                         counter[Account.UP_BYTES], counter[Account.DOWN_BYTES]))
        if not rows:
            return
        try:
            with self._db_lock:
                conn = self._connect()
                conn.executemany(
                    """
                    INSERT INTO socksus_live
                        (account_id, active_tcp, active_udp, up_bytes, down_bytes,
                         last_seen_at)
                    VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%S','now'))
                    ON CONFLICT(account_id) DO UPDATE SET
                        active_tcp = excluded.active_tcp,
                        active_udp = excluded.active_udp,
                        up_bytes = excluded.up_bytes,
                        down_bytes = excluded.down_bytes,
                        last_seen_at = excluded.last_seen_at
                    """,
                    rows,
                )
                conn.commit()
        except sqlite3.Error as exc:
            log.debug("gagal menulis socksus_live: %s", exc)


async def heartbeat_loop(store: AccountStore, interval: float) -> None:
    """Setor status akun ke database secara berkala, di luar jalur data."""
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(interval)
        try:
            # sqlite memblokir; jangan sampai menahan event loop.
            await loop.run_in_executor(None, store.flush_live)
        except Exception as exc:  # noqa: BLE001 - heartbeat tidak boleh mematikan relay
            log.debug("heartbeat error: %s", exc)


class Relay:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.up = Upstream.parse(args.up)
        self.connect_timeout = args.connect_timeout
        # Port UDP: default sama dengan port TCP. Banyak klien game mengabaikan
        # BND.PORT dari balasan UDP ASSOCIATE dan tetap mengirim datagram ke port
        # yang sama dengan koneksi TCP-nya, jadi di situlah kita mendengarkan.
        self.udp_port = args.udp_port or args.port
        self._hub: Optional["_UdpHub"] = None
        self._hub_lock = asyncio.Lock()
        # apakah upstream menerima target berupa nama domain (ATYP 3)?
        # Dicatat per upstream: tiap IP pool bisa beda perilaku.
        # None/absen = belum tahu, dipelajari dari balasan pertama.
        self._domain_ok: Dict[Tuple[str, int], bool] = {}

        # --db = multi-user (tiap buyer punya upstream sendiri). Tanpa --db,
        # akun statis dari --user/--pass/--up dipakai, persis perilaku lama.
        static = None
        if not args.db or args.keep_static_user:
            static = Account(
                None, args.user.encode(), args.pass_.encode(), self.up,
                [0, 0, 0, 0, 0],
            )
        self.store = AccountStore(args.db, static, args.db_reload)
        if args.db:
            self.store.reload(force=True)
        if args.local_dns:
            # dipaksa: semua upstream dianggap tidak menerima nama domain
            self._force_local_dns = True
        else:
            self._force_local_dns = False

    def domain_ok(self, up: Upstream) -> Optional[bool]:
        if self._force_local_dns:
            return False
        return self._domain_ok.get(up.key)

    # ------------------------------------------------------------------ util #
    @staticmethod
    async def reply(writer: asyncio.StreamWriter, code: int,
                    host: str = "0.0.0.0", port: int = 0) -> None:
        """Balasan SOCKS5. Untuk UDP ASSOCIATE, host:port = endpoint UDP kita."""
        try:
            addr = b"\x01" + socket.inet_aton(host)
        except OSError:
            try:
                addr = b"\x04" + socket.inet_pton(socket.AF_INET6, host)
            except OSError:
                raw = host.encode("ascii", errors="replace")[:255]
                addr = b"\x03" + bytes([len(raw)]) + raw
        writer.write(b"\x05" + bytes([code]) + b"\x00" + addr + struct.pack("!H", port))
        await writer.drain()

    # ------------------------------------------------------- klien -> relay #
    async def negotiate(
        self, r: asyncio.StreamReader, w: asyncio.StreamWriter
    ) -> Optional[Account]:
        """Greeting + auth username/password.

        Return akun pemilik kredensial itu (menentukan upstream mana yang dipakai),
        atau None kalau ditolak.
        """
        if await r.readexactly(1) != b"\x05":
            return None
        nmethods = (await r.readexactly(1))[0]
        methods = await r.readexactly(nmethods)
        if 0x02 not in methods:  # kita hanya menerima user/pass
            w.write(b"\x05\xff")
            await w.drain()
            return None
        w.write(b"\x05\x02")
        await w.drain()

        if await r.readexactly(1) != b"\x01":  # sub-negotiation version
            return None
        ulen = (await r.readexactly(1))[0]
        uname = await r.readexactly(ulen)
        plen = (await r.readexactly(1))[0]
        passwd = await r.readexactly(plen)

        account = self.store.lookup(uname, passwd)
        w.write(b"\x01" + (b"\x00" if account is not None else b"\x01"))
        await w.drain()
        return account

    async def read_request(
        self, r: asyncio.StreamReader
    ) -> Tuple[int, int, bytes, bytes, str, int]:
        """Baca request SOCKS5. Return (cmd, atyp, addr_field, port_bytes, host, port)."""
        head = await r.readexactly(4)  # VER, CMD, RSV, ATYP
        if head[0] != 0x05:
            raise ValueError("versi request bukan 5")
        cmd, atyp = head[1], head[3]
        if atyp == 0x01:
            addr_field = await r.readexactly(4)
            host = socket.inet_ntoa(addr_field)
        elif atyp == 0x03:
            ln = (await r.readexactly(1))[0]
            body = await r.readexactly(ln)
            addr_field = bytes([ln]) + body
            # hanya untuk log; addr_field diteruskan apa adanya ke upstream.
            # jangan pakai codec "idna": ia menolak errors= selain "strict"
            # dan melempar UnicodeError (turunan ValueError) untuk domain biasa.
            host = body.decode("ascii", errors="replace")
        elif atyp == 0x04:
            addr_field = await r.readexactly(16)
            host = socket.inet_ntop(socket.AF_INET6, addr_field)
        else:
            raise _AtypError()
        port_bytes = await r.readexactly(2)
        port = struct.unpack("!H", port_bytes)[0]
        return cmd, atyp, addr_field, port_bytes, host, port

    # ----------------------------------------------------- relay -> upstream #
    async def _connect_upstream(
        self, up: Upstream,
    ) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Konek + greeting + auth ke upstream. Siap menerima perintah."""
        ur, uw = await asyncio.wait_for(
            asyncio.open_connection(up.host, up.port),
            timeout=self.connect_timeout,
        )
        try:
            # greeting: minta metode user/pass kalau ada kredensial
            methods = b"\x00\x02" if up.user else b"\x00"
            uw.write(b"\x05" + bytes([len(methods)]) + methods)
            await uw.drain()
            ver, method = await ur.readexactly(2)
            if ver != 0x05:
                raise ConnectionError("upstream bukan SOCKS5")
            if method == 0x02:
                if not up.user:
                    raise ConnectionError("upstream minta auth, kredensial kosong")
                u, p = up.user.encode(), up.password.encode()
                uw.write(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
                await uw.drain()
                if (await ur.readexactly(2))[1] != 0x00:
                    raise ConnectionError("auth upstream ditolak")
            elif method == 0xFF:
                raise ConnectionError("upstream menolak semua metode auth")
            elif method != 0x00:
                raise ConnectionError("metode auth upstream tidak didukung")
            return ur, uw
        except BaseException:
            uw.close()
            raise

    @staticmethod
    async def _read_reply(ur: asyncio.StreamReader) -> Tuple[int, str, int]:
        """Baca balasan upstream. Return (rep, BND.ADDR, BND.PORT)."""
        head = await ur.readexactly(4)
        atyp = head[3]
        if atyp == 0x01:
            host = socket.inet_ntoa(await ur.readexactly(4))
        elif atyp == 0x04:
            host = socket.inet_ntop(socket.AF_INET6, await ur.readexactly(16))
        elif atyp == 0x03:
            n = (await ur.readexactly(1))[0]
            host = (await ur.readexactly(n)).decode("ascii", errors="replace")
        else:
            raise ConnectionError("ATYP balasan upstream tidak dikenal")
        port = struct.unpack("!H", await ur.readexactly(2))[0]
        return head[1], host, port

    @staticmethod
    async def resolve_v4(host: str, port: int) -> bytes:
        """Nama domain -> 4 byte IPv4, untuk upstream yang tidak menerima ATYP 3."""
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(
            host, port, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
        if not infos:
            raise ConnectionError(f"tidak bisa resolve {host}")
        return socket.inet_aton(infos[0][4][0])

    async def open_upstream(
        self, up: Upstream, atyp: int, addr_field: bytes, port_bytes: bytes,
        host: str, port: int,
    ) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter, int]:
        """Teruskan CONNECT ke upstream milik akun ini. Return (r, w, rep)."""
        if atyp == 0x03 and self.domain_ok(up) is False:
            atyp, addr_field = 0x01, await self.resolve_v4(host, port)

        ur, uw = await self._connect_upstream(up)
        try:
            # target sama persis seperti diminta klien (ATYP dipertahankan)
            uw.write(b"\x05\x01\x00" + bytes([atyp]) + addr_field + port_bytes)
            await uw.drain()
            rep, _, _ = await self._read_reply(ur)
        except BaseException:
            uw.close()
            raise

        if atyp == 0x03:
            if rep == REP_ATYP_NOT_SUPPORTED:
                # upstream tidak paham nama domain: resolve sendiri dan ulangi.
                # sekali ketahuan, request berikutnya ke upstream ini langsung
                # lewat jalur IPv4.
                self._domain_ok[up.key] = False
                log.info("upstream %s menolak nama domain - DNS diresolve lokal "
                         "mulai sekarang", up)
                uw.close()
                return await self.open_upstream(
                    up, 0x01, await self.resolve_v4(host, port), port_bytes, host, port
                )
            if rep == REP_OK:
                self._domain_ok[up.key] = True
        return ur, uw, rep

    async def open_upstream_udp(
        self, up: Upstream,
    ) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter, int, str, int]:
        """UDP ASSOCIATE ke upstream. Return (r, w, rep, host, port relay UDP-nya)."""
        ur, uw = await self._connect_upstream(up)
        try:
            # DST 0.0.0.0:0 - kita belum tahu dari port mana klien akan mengirim
            uw.write(b"\x05\x03\x00\x01\x00\x00\x00\x00\x00\x00")
            await uw.drain()
            rep, host, port = await self._read_reply(ur)
            if host in ("0.0.0.0", "::"):
                host = up.host  # upstream tidak menyebut IP-nya sendiri
            return ur, uw, rep, host, port
        except BaseException:
            uw.close()
            raise

    # -------------------------------------------------------------- tunnel #
    @staticmethod
    async def tunnel(a_r, a_w, b_r, b_w, account: Optional[Account] = None) -> None:
        async def one(r: asyncio.StreamReader, w: asyncio.StreamWriter,
                      to_upstream: bool) -> None:
            try:
                while True:
                    data = await r.read(BUF)
                    if not data:
                        break
                    if to_upstream:
                        stats.up_bytes += len(data)
                        if account is not None:
                            account.add_up(len(data))
                    else:
                        stats.down_bytes += len(data)
                        if account is not None:
                            account.add_down(len(data))
                    w.write(data)
                    await w.drain()
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                try:
                    if w.can_write_eof():
                        w.write_eof()
                except (OSError, RuntimeError):
                    pass

        # a = klien, b = upstream: arah a->b dihitung sebagai "naik"
        await asyncio.gather(one(a_r, b_w, True), one(b_r, a_w, False),
                             return_exceptions=True)

    # ----------------------------------------------------------------- UDP #
    async def get_hub(self) -> "_UdpHub":
        """Socket UDP bersama, dibuat sekali lalu dipakai semua asosiasi."""
        async with self._hub_lock:
            if self._hub is None:
                bind_ip = self.args.listen
                # socket loopback tidak bisa mengirim ke upstream di internet
                if not bind_ip or bind_ip.startswith("127."):
                    bind_ip = "0.0.0.0"
                loop = asyncio.get_running_loop()
                _, hub = await loop.create_datagram_endpoint(
                    _UdpHub, local_addr=(bind_ip, self.udp_port)
                )
                self._hub = hub
                log.info("UDP siap di %s:%d", bind_ip, self.udp_port)
            return self._hub

    async def handle_udp(
        self, r: asyncio.StreamReader, w: asyncio.StreamWriter,
        client: str, client_ip: str, account: Account,
    ) -> None:
        """UDP ASSOCIATE: buka socket UDP lokal, chain ke relay UDP upstream."""
        try:
            ur, uw, rep, up_host, up_port = await self.open_upstream_udp(account.up)
        except asyncio.TimeoutError:
            log.warning("%s UDP: upstream timeout", client)
            await self.reply(w, REP_HOST_UNREACH)
            return
        except (OSError, ConnectionError, asyncio.IncompleteReadError) as exc:
            log.warning("%s UDP: upstream error: %s", client, exc)
            await self.reply(w, REP_GENERAL_FAIL)
            return

        if rep != REP_OK:
            log.info("%s UDP: upstream menolak (rep=%d)", client, rep)
            uw.close()
            await self.reply(w, rep)
            return

        try:
            hub = await self.get_hub()
        except OSError as exc:
            log.warning("%s UDP: gagal bind port %d: %s", client, self.udp_port, exc)
            uw.close()
            await self.reply(w, REP_GENERAL_FAIL)
            return

        assoc = _Assoc(client_ip, (up_host, up_port), self.args.udp_strict, account)
        hub.register(assoc)
        # alamat yang dikirim ke klien harus bisa dijangkau klien dari internet
        advertise = self.args.udp_ip or w.get_extra_info("sockname")[0]
        log.info("%s UDP ASSOCIATE -> klien kirim ke %s:%d, upstream %s:%d",
                 client, advertise, self.udp_port, up_host, up_port)
        try:
            await self.reply(w, REP_OK, advertise, self.udp_port)
            # asosiasi hidup selama koneksi TCP kontrol (klien / upstream) hidup
            await asyncio.wait(
                [asyncio.create_task(_read_to_eof(r)),
                 asyncio.create_task(_read_to_eof(ur))],
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            hub.unregister(assoc)
            uw.close()
            log.debug("%s UDP selesai: %d paket keluar, %d masuk, %d dibuang",
                      client, assoc.to_up, assoc.to_client, assoc.dropped)
            if assoc.domain_pkts and not assoc.to_client:
                log.warning("%s UDP: %d paket bertarget nama domain dan tidak ada balasan "
                            "sama sekali - upstream ini kemungkinan hanya menerima IP",
                            client, assoc.domain_pkts)

    # ---------------------------------------------------------- per klien #
    async def handle(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        peer = w.get_extra_info("peername")
        client = f"{peer[0]}:{peer[1]}" if peer else "-"
        uw: Optional[asyncio.StreamWriter] = None
        try:
            account = await self.negotiate(r, w)
            if account is None:
                log.info("%s auth gagal / ditolak", client)
                return

            try:
                cmd, atyp, addr_field, port_bytes, host, port = await self.read_request(r)
            except _AtypError:
                await self.reply(w, REP_ATYP_NOT_SUPPORTED)
                return

            if cmd == 0x03 and not self.args.no_udp:  # UDP ASSOCIATE (dipakai game)
                await self.handle_udp(r, w, client, peer[0] if peer else "", account)
                return

            if cmd != 0x01:  # sisanya (BIND) tidak didukung
                log.info("%s CMD %d ditolak (hanya CONNECT/UDP)", client, cmd)
                await self.reply(w, REP_CMD_NOT_SUPPORTED)
                return

            try:
                ur, uw, rep = await self.open_upstream(
                    account.up, atyp, addr_field, port_bytes, host, port
                )
            except asyncio.TimeoutError:
                log.warning("%s [%s] -> %s:%d upstream %s timeout",
                            client, account.label, host, port, account.up)
                await self.reply(w, REP_HOST_UNREACH)
                return
            except (OSError, ConnectionError, asyncio.IncompleteReadError) as exc:
                log.warning("%s [%s] -> %s:%d upstream %s error: %s",
                            client, account.label, host, port, account.up, exc)
                await self.reply(w, REP_GENERAL_FAIL)
                return

            if rep != REP_OK:
                log.info("%s [%s] -> %s:%d ditolak upstream (rep=%d)",
                         client, account.label, host, port, rep)
                await self.reply(w, rep)
                return

            await self.reply(w, REP_OK)
            log.debug("%s [%s via %s] -> %s:%d tersambung",
                      client, account.label, account.up, host, port)
            stats.tcp_active += 1
            stats.tcp_total += 1
            account.counter[Account.TCP] += 1
            try:
                await self.tunnel(r, w, ur, uw, account)
            finally:
                stats.tcp_active -= 1
                account.counter[Account.TCP] -= 1
                account.counter[Account.DIRTY] = 1
        except (asyncio.IncompleteReadError, ConnectionError):
            pass  # klien atau upstream memutus koneksi - normal
        except ValueError as exc:
            log.warning("%s request tidak valid: %s", client, exc)
        except Exception:
            log.exception("%s error tak terduga", client)
        finally:
            if uw is not None:
                uw.close()
            w.close()
            try:
                await w.wait_closed()
            except (ConnectionError, OSError):
                pass


class _AtypError(Exception):
    """ATYP tidak didukung."""


async def _read_to_eof(r: asyncio.StreamReader) -> None:
    """Habiskan stream sampai tutup; dipakai untuk mendeteksi asosiasi UDP berakhir."""
    try:
        while await r.read(BUF):
            pass
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass


class _Assoc:
    """Satu asosiasi UDP: pasangan klien <-> endpoint UDP upstream."""

    def __init__(self, client_ip: str, up_addr: Tuple[str, int], strict: bool = False,
                 account: Optional[Account] = None):
        self.client_ip = client_ip  # IP dari koneksi TCP kontrol
        self.up_addr = up_addr
        self.strict = strict
        self.account = account
        self.client_addr: Optional[Tuple[str, int]] = None
        self.to_up = self.to_client = self.dropped = 0
        self.domain_pkts = 0  # paket bertarget nama domain, sering ditolak upstream


class _UdpHub(asyncio.DatagramProtocol):
    """Satu socket UDP bersama untuk semua asosiasi.

    Port-nya sama dengan port TCP, karena banyak klien game mengabaikan BND.PORT
    dari balasan UDP ASSOCIATE dan tetap mengirim ke port proxy-nya. Paket SOCKS5
    UDP (RSV RSV FRAG ATYP DST.ADDR DST.PORT DATA) formatnya sama di sisi klien
    maupun upstream, jadi datagram diteruskan apa adanya. Asosiasi dikenali dari
    alamat pengirim.
    """

    def __init__(self) -> None:
        self.transport: Optional[asyncio.DatagramTransport] = None
        self.by_upstream: dict = {}     # (ip, port) relay UDP upstream -> _Assoc
        self.by_client_addr: dict = {}  # (ip, port) klien -> _Assoc
        self.by_client_ip: dict = {}    # ip klien -> _Assoc (sebelum port diketahui)

    def connection_made(self, transport) -> None:
        self.transport = transport

    @staticmethod
    def target_of(data: bytes) -> str:
        """Tujuan asli dari header paket SOCKS5 UDP, untuk keperluan log."""
        try:
            atyp = data[3]
            if atyp == 0x01:
                host, rest = socket.inet_ntoa(data[4:8]), data[8:10]
            elif atyp == 0x04:
                host, rest = socket.inet_ntop(socket.AF_INET6, data[4:20]), data[20:22]
            elif atyp == 0x03:
                n = data[4]
                host, rest = data[5:5 + n].decode("ascii", errors="replace"), data[5 + n:7 + n]
            else:
                return f"ATYP-{atyp}?"
            return f"{host}:{struct.unpack('!H', rest)[0]}"
        except Exception:
            return "?"

    def register(self, assoc: _Assoc) -> None:
        self.by_upstream[assoc.up_addr] = assoc
        self.by_client_ip[assoc.client_ip] = assoc
        stats.udp_active += 1
        stats.udp_total += 1
        if assoc.account is not None:
            assoc.account.counter[Account.UDP] += 1

    def unregister(self, assoc: _Assoc) -> None:
        stats.udp_active -= 1
        if assoc.account is not None:
            assoc.account.counter[Account.UDP] -= 1
            assoc.account.counter[Account.DIRTY] = 1
        self.by_upstream.pop(assoc.up_addr, None)
        if self.by_client_ip.get(assoc.client_ip) is assoc:
            self.by_client_ip.pop(assoc.client_ip, None)
        # klien bisa berpindah port di tengah jalan, jadi buang semua entrinya
        for key in [k for k, v in self.by_client_addr.items() if v is assoc]:
            self.by_client_addr.pop(key, None)

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        # 1. balasan dari relay UDP upstream -> teruskan ke kliennya
        assoc = self.by_upstream.get(addr)
        if assoc is not None:
            if assoc.client_addr is None:
                assoc.dropped += 1  # balasan sebelum klien mengirim apa pun
                return
            if assoc.to_client == 0:
                log.info("UDP: balasan pertama dari upstream masuk (%d byte) - jalur hidup",
                         len(data))
            self.transport.sendto(data, assoc.client_addr)
            assoc.to_client += 1
            stats.down_bytes += len(data)
            if assoc.account is not None:
                assoc.account.add_down(len(data))
            return

        # 2. dari klien yang alamat lengkapnya sudah dikenal
        assoc = self.by_client_addr.get(addr)
        if assoc is None:
            # 3. paket pertama: cocokkan lewat IP-nya saja. IP sumber boleh beda
            #    dari koneksi TCP kontrol (klien ber-NAT / multi-interface),
            #    kecuali --udp-strict.
            assoc = self.by_client_ip.get(addr[0]) or self._adopt_unmatched(addr)
            if assoc is None:
                return
            self.by_client_addr[addr] = assoc
            assoc.client_addr = addr

        if len(data) < 10:  # header SOCKS5 UDP minimal (ATYP IPv4)
            assoc.dropped += 1
            return
        if assoc.to_up == 0:
            log.debug("UDP: klien %s mengirim ke %s (%d byte)",
                      addr[0], self.target_of(data), len(data))
        if data[3] == 0x03:
            assoc.domain_pkts += 1
            if assoc.domain_pkts == 1:
                log.warning("UDP: tujuannya nama domain (%s) - upstream ini kemungkinan "
                            "membuangnya diam-diam, jadi balasan tidak akan datang",
                            self.target_of(data))
        self.transport.sendto(data, assoc.up_addr)
        assoc.to_up += 1
        stats.up_bytes += len(data)
        if assoc.account is not None:
            assoc.account.add_up(len(data))

    def _adopt_unmatched(self, addr: Tuple[str, int]) -> Optional[_Assoc]:
        """Paket dari IP tak dikenal: berikan ke asosiasi non-strict yang masih kosong."""
        for assoc in self.by_client_ip.values():
            if not assoc.strict and assoc.client_addr is None:
                log.info("UDP: sumber klien %s beda dari IP kontrol %s - diterima",
                         addr[0], assoc.client_ip)
                return assoc
        return None

    def error_received(self, exc: Exception) -> None:
        pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Server SOCKS5 lokal yang chaining ke upstream SOCKS5.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Contoh:")[-1].strip(),
    )
    p.add_argument("--listen", default="0.0.0.0",
                   help="IP untuk bind (default 0.0.0.0 = semua interface)")
    p.add_argument("--public-ip", help="IP publik untuk ditampilkan (default: deteksi online)")
    p.add_argument("--port", type=int, default=1080, help="port listen (default 1080)")
    p.add_argument("--user", default=CLIENT_USER, help=f"username klien (default {CLIENT_USER})")
    p.add_argument("--pass", dest="pass_", default=CLIENT_PASS,
                   help=f"password klien (default {CLIENT_PASS})")
    p.add_argument(
        "--up", default=f"{UP_HOST}:{UP_PORT}:{UP_USER}:{UP_PASS}",
        metavar="HOST:PORT[:USER:PASS]", help="upstream SOCKS5",
    )
    p.add_argument("--connect-timeout", type=float, default=10.0)
    p.add_argument("--db", metavar="PATH",
                   help="database bot Discord (discord_sqlite_bot.db). Kalau diisi, "
                        "relay jadi multi-user: tiap akun di tabel socksus_account "
                        "punya upstream sendiri dari socksus_pool")
    p.add_argument("--db-reload", type=float, default=10.0, metavar="DETIK",
                   help="jarak baca ulang daftar akun dari --db (default 10)")
    p.add_argument("--heartbeat", type=float, default=15.0, metavar="DETIK",
                   help="jarak tulis status per akun ke tabel socksus_live, dipakai "
                        "!poollist untuk menandai ONLINE (default 15; 0 = matikan)")
    p.add_argument("--keep-static-user", action="store_true",
                   help="di mode --db, tetap terima juga --user/--pass ke upstream "
                        "--up (akun admin untuk mengetes relay)")
    p.add_argument("--udp-port", type=int, default=0, metavar="N",
                   help="port UDP yang didengarkan (default: sama dengan --port, "
                        "karena banyak klien game mengirim UDP ke port proxy-nya)")
    p.add_argument("--udp-ip", metavar="IP",
                   help="IP yang diiklankan ke klien untuk UDP (default: IP interface "
                        "yang dipakai koneksi TCP-nya; WAJIB diisi IP publik di VPS NAT)")
    p.add_argument("--udp-strict", action="store_true",
                   help="paket UDP wajib datang dari IP yang sama dengan koneksi TCP-nya "
                        "(default: paket pertama menentukan pemilik asosiasi)")
    p.add_argument("--no-udp", action="store_true",
                   help="tolak UDP ASSOCIATE (perilaku lama, hanya CONNECT)")
    p.add_argument("--local-dns", action="store_true",
                   help="langsung resolve nama domain di sini, tanpa mencoba ATYP 3 dulu; "
                        "hanya perlu untuk upstream yang menolak nama domain (rep=8)")
    p.add_argument("--log-file", metavar="PATH",
                   help="tulis log ke file juga (default: relay.log di folder skrip; "
                        "pakai '-' untuk mematikan)")
    p.add_argument("--stats", type=float, default=5.0, metavar="DETIK",
                   help="jarak baris ringkasan bandwidth/pool (default 5; 0 = matikan)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="tampilkan juga log per koneksi (default: cuma ringkasan berkala "
                        "dan peringatan)")
    p.add_argument("-q", "--quiet", action="store_true", help="hanya warning/error")
    return p


async def run(args: argparse.Namespace) -> int:
    relay = Relay(args)
    bind = args.listen
    server = await asyncio.start_server(relay.handle, bind, args.port, backlog=512)

    # IP yang diberikan ke user: override manual > deteksi online > IP interface lokal
    loop = asyncio.get_running_loop()
    public = args.public_ip or await loop.run_in_executor(None, detect_public_ip) \
        or detect_ip()

    up = relay.up
    log.info("SOCKS5 relay listening di %s:%d", bind, args.port)
    if args.db:
        log.info("  mode        : MULTI-USER (akun dari %s)", args.db)
        log.info("  akun aktif  : %d (dibaca ulang tiap %g detik)",
                 relay.store.active_accounts(), args.db_reload)
        log.info("  klien pakai : %s:%d:<user>:<password> dari bot Discord",
                 public, args.port)
        if args.keep_static_user:
            log.info("  akun statis : %s:%d:%s:%s -> upstream %s",
                     public, args.port, args.user, args.pass_, up)
        if relay.store.active_accounts() == 0:
            log.warning("  Belum ada akun aktif - jual dulu lewat panel !socksus, "
                        "atau isi pool dengan !pool.")
    else:
        log.info("  mode        : SINGLE-USER")
        log.info("  klien pakai (ip:port:user:pw) : %s:%d:%s:%s",
                 public, args.port, args.user, args.pass_)
        log.info("  upstream    (ip:port:user:pw) : %s:%d:%s:%s",
                 up.host, up.port, up.user or "-", up.password or "-")

    if args.no_udp:
        log.info("  UDP: dimatikan (--no-udp) - game yang butuh UDP tidak akan jalan")
    else:
        log.info("  UDP: aktif di port %d (buka port ini di firewall juga)",
                 relay.udp_port)
        local = detect_ip()
        if args.udp_ip:
            log.info("  UDP: alamat yang diiklankan ke klien = %s", args.udp_ip)
        elif public != local:
            log.warning("  UDP: IP interface (%s) beda dari IP publik (%s) - mesin ber-NAT.",
                        local, public)
            log.warning("       Klien akan diberi %s dan paket UDP-nya tidak akan sampai.",
                        local)
            log.warning("       Jalankan ulang dengan: --udp-ip %s", public)

    background = []
    if args.stats > 0:
        log.info("  Ringkasan bandwidth/pool dicetak tiap %g detik.", args.stats)
        background.append(asyncio.create_task(report_stats(args.stats)))
    if args.db and args.heartbeat > 0:
        background.append(
            asyncio.create_task(heartbeat_loop(relay.store, args.heartbeat))
        )

    async with server:
        try:
            await server.serve_forever()
        except asyncio.CancelledError:
            log.info("shutting down")
        finally:
            for task in background:
                task.cancel()
            if args.db:
                relay.store.flush_live()
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    handlers = [logging.StreamHandler()]
    # log ke file supaya gampang ditinjau setelah kejadian, tanpa menyalin apa pun
    log_path = args.log_file or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "relay.log"
    )
    if log_path != "-":
        try:
            # menumpuk (append), bukan menimpa: riwayat sesi sebelumnya tetap ada.
            # Dipotong otomatis di 5 MB dengan satu berkas cadangan supaya tidak
            # tumbuh tanpa batas.
            handlers.append(logging.handlers.RotatingFileHandler(
                log_path, maxBytes=5 * 1024 * 1024, backupCount=1, encoding="utf-8"))
        except OSError as exc:
            print(f"peringatan: tidak bisa menulis {log_path}: {exc}", file=sys.stderr)

    if args.quiet:
        level = logging.WARNING
    elif args.verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
