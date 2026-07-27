"""Toko SOCKS5 private (produk `socksus`).

Beda dengan cmd_socks.py yang menjual proxy mentah satu per satu: di sini semua
buyer konek ke SATU pintu masuk yang sama (nifstore.duckdns.org:1080, yaitu
socks5_relay.py di mesin ini), tapi tiap buyer memakai username/password sendiri.
Relay memakai kredensial itu untuk memilih upstream mana dari IP pool yang
dipakai buyer tersebut - jadi satu buyer = satu IP pool, terkunci (lock).

    buyer A  --nifstore.duckdns.org:1080:nifa1b2c3:xxx-->  RELAY  --> pool #12
    buyer B  --nifstore.duckdns.org:1080:nif9f8e7d:yyy-->  RELAY  --> pool #13

Admin mengisi pool lewat `!pool` (lampirkan .txt berisi host:port:user:pass) dan
melihat pemetaannya lewat `!poollist`. Buyer bisa ganti IP lewat tombol Change IP
di panel (gratis 5x): kredensialnya tetap, yang berpindah adalah IP pool di
belakangnya.
"""

import asyncio
import datetime
import io
import os
import secrets
import string
import time

import discord
from discord.ui import Button, Modal, Select, TextInput, View
from discord.ext import tasks

from utils import is_allowed_user


PRODUCT_CODE = "socksus"
PRODUCT_TITLE = os.getenv("SOCKSUS_TITLE", "Socks5 Private 🌐")

# 1 DL = 100 WL. Harga tetap bisa diubah admin lewat !hargasocksus / !setharga.
WL_PER_DL = int(os.getenv("WL_PER_DL", "100"))
DEFAULT_PRICE_WL = int(os.getenv("SOCKSUS_PRICE_WL", str(WL_PER_DL)))

SOCKSUS_HOST = os.getenv("SOCKSUS_HOST", "nifstore.duckdns.org")
SOCKSUS_PORT = int(os.getenv("SOCKSUS_PORT", "1080"))
DURATION_DAYS = int(os.getenv("SOCKSUS_DURATION_DAYS", "30"))
CHANGE_QUOTA = int(os.getenv("SOCKSUS_CHANGE_QUOTA", "5"))
CHANGE_COOLDOWN_SECONDS = int(os.getenv("SOCKSUS_CHANGE_COOLDOWN_SECONDS", "60"))
PANEL_UPDATE_MINUTES = int(os.getenv("SOCKSUS_PANEL_UPDATE_MINUTES", "5"))
# Kalau diisi, panel otomatis terpasang di channel ini saat bot pertama kali start
# walau `!socksus` belum pernah dijalankan.
PANEL_CHANNEL_ID = int(os.getenv("SOCKSUS_CHANNEL_ID", "0") or 0)
# Relay dianggap "online" untuk satu akun kalau heartbeat-nya belum lebih tua dari ini.
LIVE_STALE_SECONDS = int(os.getenv("SOCKSUS_LIVE_STALE_SECONDS", "60"))

CHANNEL_TESTIMONI = int(os.getenv("CHANNEL_TESTIMONI", "0") or 0)
ROLE_BUY = int(os.getenv("ROLE_BUY", "0") or 0)

MAX_BUY_QTY = 10

_lock = asyncio.Lock()

EMO_EXCLAMATION = "<a:exclamation:1419518587072282654>"
EMO_PRODUCT = "<a:toa:1122531485090582619>"
EMO_ARROW = "<a:panah1:1419515217892606053>"
EMO_WL = "<a:world_lock:1419515667773657109>"

_USER_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
_PASS_ALPHABET = string.ascii_lowercase + string.ascii_uppercase + string.digits


def _btn_emoji(name, emoji_id, animated=False):
    return discord.PartialEmoji(name=name, id=emoji_id, animated=animated)


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_ts(value):
    """String waktu dari SQLite -> datetime aware. None kalau tidak terbaca."""
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _ensure_schema(c, conn):
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS socksus_pool (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host TEXT NOT NULL,
            port INTEGER NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            password TEXT NOT NULL DEFAULT '',
            proxy TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'available',
            assigned_account_id INTEGER,
            note TEXT,
            added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used_at TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS socksus_account (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_user_id INTEGER NOT NULL,
            client_user TEXT NOT NULL UNIQUE,
            client_pass TEXT NOT NULL,
            pool_id INTEGER,
            status TEXT NOT NULL DEFAULT 'active',
            locked INTEGER NOT NULL DEFAULT 1,
            change_quota INTEGER NOT NULL DEFAULT 5,
            change_used INTEGER NOT NULL DEFAULT 0,
            last_change_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS socksus_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            old_pool_id INTEGER,
            new_pool_id INTEGER,
            reason TEXT NOT NULL DEFAULT 'change',
            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Ditulis oleh socks5_relay.py (mode --db), dibaca di sini untuk status ONLINE.
    c.execute(
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
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS socksus_panel_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            channel_id INTEGER,
            message_id INTEGER
        )
        """
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_socksus_pool_status ON socksus_pool(status)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_socksus_account_buyer "
        "ON socksus_account(buyer_user_id, status)"
    )
    c.execute(
        "INSERT OR IGNORE INTO stock (kode, judul, harga) VALUES (?, ?, ?)",
        (PRODUCT_CODE, PRODUCT_TITLE, DEFAULT_PRICE_WL),
    )
    c.execute(
        "INSERT OR IGNORE INTO socksus_panel_settings (id, channel_id, message_id) "
        "VALUES (1, NULL, NULL)"
    )
    conn.commit()


def parse_pool_line(line):
    """Satu baris file pool -> (host, port, user, password). None kalau tidak valid.

    Diterima: host:port, host:port:user:pass, user:pass@host:port, dengan atau
    tanpa awalan socks5:// . Baris kosong dan komentar (# atau //) dilewati.
    """
    line = (line or "").strip()
    if not line or line.startswith("#") or line.startswith("//"):
        return None
    line = line.split()[0].strip().strip(",;")

    lowered = line.lower()
    for scheme in ("socks5h://", "socks5://", "socks4://", "http://", "https://"):
        if lowered.startswith(scheme):
            line = line[len(scheme):]
            break

    if "@" in line:
        cred, _, endpoint = line.rpartition("@")
        host, _, port = endpoint.partition(":")
        user, _, password = cred.partition(":")
        if host and port.isdigit():
            return host, int(port), user, password
        return None

    parts = line.split(":")
    if len(parts) == 2 and parts[0] and parts[1].isdigit():
        return parts[0], int(parts[1]), "", ""
    if len(parts) == 4 and parts[0] and parts[1].isdigit():
        return parts[0], int(parts[1]), parts[2], parts[3]
    return None


def _gen_client_user(c):
    """Username klien yang belum terpakai. Prefix 'nif' biar gampang dikenali di log."""
    for _ in range(50):
        candidate = "nif" + "".join(secrets.choice(_USER_ALPHABET) for _ in range(6))
        row = c.execute(
            "SELECT 1 FROM socksus_account WHERE client_user = ?", (candidate,)
        ).fetchone()
        if not row:
            return candidate
    raise RuntimeError("Gagal membuat username unik untuk SOCKS.")


def _gen_client_pass():
    return "".join(secrets.choice(_PASS_ALPHABET) for _ in range(12))


def _conn_string(client_user, client_pass):
    return f"{SOCKSUS_HOST}:{SOCKSUS_PORT}:{client_user}:{client_pass}"


class SocksUsStoreView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            Button(
                label="Buy SOCKS5",
                emoji=_btn_emoji("toa", 1122531485090582619, True),
                style=discord.ButtonStyle.green,
                custom_id="socksus_buy",
            )
        )
        self.add_item(
            Button(
                label="Change IP",
                emoji=_btn_emoji("panah1", 1419515217892606053, True),
                style=discord.ButtonStyle.danger,
                custom_id="socksus_change",
            )
        )
        self.add_item(
            Button(
                label="My SOCKS",
                emoji=_btn_emoji("toa", 1122531485090582619, True),
                style=discord.ButtonStyle.secondary,
                custom_id="socksus_mine",
            )
        )
        self.add_item(
            Button(
                label="Depo QRIS",
                emoji=_btn_emoji("world_lock", 1419515667773657109, True),
                style=discord.ButtonStyle.blurple,
                custom_id="socksus_depo_qris",
            )
        )
        self.add_item(
            Button(
                label="Deposit WL",
                emoji=_btn_emoji("world_lock", 1419515667773657109, True),
                style=discord.ButtonStyle.blurple,
                custom_id="socksus_deposit",
            )
        )
        self.add_item(
            Button(
                label="Set GrowID",
                emoji=_btn_emoji("toa", 1122531485090582619, True),
                style=discord.ButtonStyle.gray,
                custom_id="socksus_growid",
            )
        )
        self.add_item(
            Button(
                label="My Balance",
                emoji=_btn_emoji("world_lock", 1419515667773657109, True),
                style=discord.ButtonStyle.secondary,
                custom_id="socksus_balance",
            )
        )


def setup(bot, c, conn, fmt_wl, PREFIX):
    _ensure_schema(c, conn)
    panel_cache = {"channel_id": None, "message_id": None, "message": None}
    ready_initialized = False

    # ------------------------------------------------------------------ data #
    def get_price():
        row = c.execute(
            "SELECT harga FROM stock WHERE kode = ?", (PRODUCT_CODE,)
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def price_label(price):
        if price and WL_PER_DL and price % WL_PER_DL == 0:
            return f"{fmt_wl(price)} WL ({price // WL_PER_DL} DL)"
        return f"{fmt_wl(price)} WL"

    def is_online(account_id):
        row = c.execute(
            "SELECT active_tcp, active_udp, last_seen_at FROM socksus_live "
            "WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if not row:
            return False, 0
        last_seen = _parse_ts(row[2])
        if last_seen is None:
            return False, 0
        fresh = (_utcnow() - last_seen).total_seconds() <= LIVE_STALE_SECONDS
        return fresh, int(row[0] or 0) + int(row[1] or 0)

    def expire_due_accounts():
        """Akun lewat masa aktif -> nonaktif, IP pool-nya dilepas balik ke stok."""
        now = _iso(_utcnow())
        rows = c.execute(
            """
            SELECT id, pool_id FROM socksus_account
            WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (now,),
        ).fetchall()
        if not rows:
            return 0
        for account_id, pool_id in rows:
            c.execute(
                "UPDATE socksus_account SET status = 'expired', pool_id = NULL "
                "WHERE id = ?",
                (account_id,),
            )
            if pool_id:
                c.execute(
                    "UPDATE socksus_pool SET status = 'available', "
                    "assigned_account_id = NULL WHERE id = ?",
                    (pool_id,),
                )
                c.execute(
                    "INSERT INTO socksus_history "
                    "(account_id, old_pool_id, new_pool_id, reason) VALUES (?, ?, NULL, 'expired')",
                    (account_id, pool_id),
                )
            c.execute("DELETE FROM socksus_live WHERE account_id = ?", (account_id,))
        conn.commit()
        return len(rows)

    # ----------------------------------------------------------------- panel #
    def build_panel_embed():
        available = c.execute(
            "SELECT COUNT(*) FROM socksus_pool WHERE status = 'available'"
        ).fetchone()[0]
        assigned = c.execute(
            "SELECT COUNT(*) FROM socksus_pool WHERE status = 'assigned'"
        ).fetchone()[0]
        dead = c.execute(
            "SELECT COUNT(*) FROM socksus_pool WHERE status = 'dead'"
        ).fetchone()[0]
        sold = c.execute(
            """
            SELECT COALESCE(SUM(jumlah), 0) FROM transactions
            WHERE LOWER(kode) = LOWER(?)
            """,
            (PRODUCT_CODE,),
        ).fetchone()[0]
        price = get_price()

        lines = [
            f"{EMO_PRODUCT}  **{PRODUCT_TITLE}** (`SOCKSUS`)",
            f"{EMO_ARROW}  **Stock:** `{available}`",
            f"{EMO_ARROW}  **Price:** `{price_label(price)}` {EMO_WL}",
            f"{EMO_ARROW}  **Active:** `{DURATION_DAYS} Days`",
            f"{EMO_ARROW}  **Free Change IP:** `{CHANGE_QUOTA}x`",
            f"{EMO_ARROW}  **Product Sold:** `{sold}`",
            f"{EMO_ARROW}  **IP Terpakai:** `{assigned}`",
        ]
        if dead:
            lines.append(f"{EMO_ARROW}  **IP Mati:** `{dead}`")

        embed = discord.Embed(
            title=f"{EMO_EXCLAMATION} SOCKS5 PRIVATE LIST {EMO_EXCLAMATION}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f" Last Update: {datetime.datetime.now().strftime('%H:%M:%S')} "
            f"| refresh tiap {PANEL_UPDATE_MINUTES} menit"
        )
        return embed

    async def fetch_channel(channel_id):
        channel = bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await bot.fetch_channel(channel_id)
        except Exception:
            return None

    async def fetch_panel_message():
        channel_id = panel_cache["channel_id"]
        message_id = panel_cache["message_id"]
        if not channel_id or not message_id:
            return None
        if panel_cache["message"] is not None:
            return panel_cache["message"]
        channel = await fetch_channel(channel_id)
        if channel is None:
            return None
        try:
            panel_cache["message"] = await channel.fetch_message(message_id)
            return panel_cache["message"]
        except Exception:
            return None

    def save_panel(channel_id, message_id, message=None):
        panel_cache.update(
            {
                "channel_id": channel_id,
                "message_id": message_id,
                "message": message,
            }
        )
        c.execute(
            "UPDATE socksus_panel_settings SET channel_id = ?, message_id = ? "
            "WHERE id = 1",
            (channel_id, message_id),
        )
        conn.commit()

    async def move_panel(channel):
        old_message = await fetch_panel_message()
        if old_message is not None:
            try:
                await old_message.delete()
            except Exception:
                pass
        message = await channel.send(embed=build_panel_embed(), view=SocksUsStoreView())
        save_panel(channel.id, message.id, message)
        return message

    async def refresh_panel():
        """Update panel di tempat. Kalau pesannya hilang, dipasang ulang sendiri."""
        message = await fetch_panel_message()
        if message is not None:
            try:
                await message.edit(embed=build_panel_embed(), view=SocksUsStoreView())
                return
            except Exception as exc:
                print(f"[SOCKSUS PANEL] Gagal update: {exc}")
                panel_cache["message"] = None
                return

        # Pesan panel tidak ada (terhapus / bot restart sebelum sempat kirim):
        # pasang ulang otomatis supaya admin tidak perlu !socksus lagi.
        channel_id = panel_cache["channel_id"] or PANEL_CHANNEL_ID
        if not channel_id:
            return
        channel = await fetch_channel(channel_id)
        if channel is None:
            return
        try:
            message = await channel.send(
                embed=build_panel_embed(), view=SocksUsStoreView()
            )
            save_panel(channel.id, message.id, message)
            print(f"[SOCKSUS PANEL] Panel dipasang ulang di #{channel}")
        except Exception as exc:
            print(f"[SOCKSUS PANEL] Gagal pasang ulang panel: {exc}")

    # ------------------------------------------------------------ transaksi #
    async def send_testimonial(interaction, transaction_id, quantity, total):
        if not CHANNEL_TESTIMONI:
            return
        channel = await fetch_channel(CHANNEL_TESTIMONI)
        if channel is None:
            return
        embed = discord.Embed(
            title=f"#Order Number: {transaction_id}",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="<a:megaphone:1419515391851626580> Pembeli",
            value=interaction.user.mention,
            inline=False,
        )
        embed.add_field(
            name="Produk <a:menkrep:1122531571098980394>",
            value=f"{quantity} {PRODUCT_TITLE}",
            inline=False,
        )
        embed.add_field(
            name="Total Price", value=f"{fmt_wl(total)} {EMO_WL}", inline=False
        )
        embed.set_footer(text="Thanks For Purchasing Our Product(s)")
        try:
            await channel.send(embed=embed)
        except Exception as exc:
            print(f"[SOCKSUS TESTIMONI] Gagal kirim: {exc}")

    def rollback_delivery(user_id, account_ids, total, transaction_id):
        """DM gagal -> saldo balik, akun dibatalkan, IP pool balik ke stok."""
        try:
            conn.execute("BEGIN IMMEDIATE")
            c.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (total, user_id),
            )
            for account_id in account_ids:
                row = c.execute(
                    "SELECT pool_id FROM socksus_account WHERE id = ?", (account_id,)
                ).fetchone()
                if row and row[0]:
                    c.execute(
                        "UPDATE socksus_pool SET status = 'available', "
                        "assigned_account_id = NULL WHERE id = ?",
                        (row[0],),
                    )
                c.execute("DELETE FROM socksus_account WHERE id = ?", (account_id,))
            if transaction_id:
                c.execute(
                    "DELETE FROM transaction_items WHERE transaction_id = ?",
                    (transaction_id,),
                )
                c.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def format_account_block(index, client_user, client_pass, pool_row, expires_at):
        """pool_row = (host, port). Blok teks untuk DM / file.

        IP pool asli SENGAJA tidak ditampilkan ke buyer - mereka cukup tahu bahwa
        IP-nya dedicated & terkunci, bukan alamat aslinya. Alamat asli hanya terlihat
        admin lewat `!poollist`.
        """
        ip_status = "Dedicated (locked)" if pool_row else "belum ada - klik Change IP"
        return (
            f"# SOCKS5 #{index}\n"
            f"Proxy    : {_conn_string(client_user, client_pass)}\n"
            f"Host     : {SOCKSUS_HOST}\n"
            f"Port     : {SOCKSUS_PORT}\n"
            f"Username : {client_user}\n"
            f"Password : {client_pass}\n"
            f"Exit IP  : {ip_status}\n"
            f"Expired  : {expires_at}\n"
        )

    class SocksUsBuyModal(Modal, title="Buy SOCKS5 Private"):
        amount = TextInput(
            label="Jumlah",
            placeholder="1",
            required=True,
            min_length=1,
            max_length=2,
        )

        async def on_submit(self, interaction: discord.Interaction):
            try:
                quantity = int(str(self.amount.value).strip())
                if quantity < 1 or quantity > MAX_BUY_QTY:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    f"Jumlah harus 1 sampai {MAX_BUY_QTY}.", ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True)
            user_id = interaction.user.id
            try:
                dm_channel = await interaction.user.create_dm()
            except Exception:
                await interaction.followup.send(
                    "DM tidak dapat dibuka. Aktifkan DM lalu coba lagi.", ephemeral=True
                )
                return

            account_ids = []
            blocks = []
            transaction_id = None
            total = 0
            expires_at = _iso(_utcnow() + datetime.timedelta(days=DURATION_DAYS))

            async with _lock:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    user_row = c.execute(
                        "SELECT nama, balance FROM users WHERE user_id = ?", (user_id,)
                    ).fetchone()
                    if not user_row:
                        raise ValueError("Klik **Set GrowID** terlebih dahulu.")

                    price = get_price()
                    if price <= 0:
                        raise ValueError(
                            f"Harga belum diatur admin. Gunakan `{PREFIX}hargasocksus <wl>`."
                        )
                    total = price * quantity
                    if int(user_row[1] or 0) < total:
                        raise ValueError(f"Saldo kurang. Dibutuhkan {fmt_wl(total)} WL.")

                    pool_rows = c.execute(
                        """
                        SELECT id, host, port FROM socksus_pool
                        WHERE status = 'available'
                        ORDER BY COALESCE(last_used_at, added_at), id
                        LIMIT ?
                        """,
                        (quantity,),
                    ).fetchall()
                    if len(pool_rows) < quantity:
                        raise ValueError(
                            f"Stock IP pool tidak cukup. Tersedia {len(pool_rows)}."
                        )

                    c.execute(
                        "UPDATE users SET balance = balance - ? WHERE user_id = ?",
                        (total, user_id),
                    )
                    c.execute(
                        "INSERT INTO transactions (user_id, kode, jumlah, harga) "
                        "VALUES (?, ?, ?, ?)",
                        (user_id, PRODUCT_CODE, quantity, price),
                    )
                    transaction_id = c.lastrowid

                    for index, (pool_id, host, port) in enumerate(pool_rows, start=1):
                        client_user = _gen_client_user(c)
                        client_pass = _gen_client_pass()
                        c.execute(
                            """
                            INSERT INTO socksus_account
                                (buyer_user_id, client_user, client_pass, pool_id,
                                 status, locked, change_quota, change_used, expires_at)
                            VALUES (?, ?, ?, ?, 'active', 1, ?, 0, ?)
                            """,
                            (
                                user_id,
                                client_user,
                                client_pass,
                                pool_id,
                                CHANGE_QUOTA,
                                expires_at,
                            ),
                        )
                        account_id = c.lastrowid
                        account_ids.append(account_id)

                        c.execute(
                            """
                            UPDATE socksus_pool
                            SET status = 'assigned', assigned_account_id = ?,
                                last_used_at = CURRENT_TIMESTAMP
                            WHERE id = ? AND status = 'available'
                            """,
                            (account_id, pool_id),
                        )
                        if c.rowcount != 1:
                            raise RuntimeError(
                                "IP pool berubah saat transaksi. Silakan coba lagi."
                            )
                        c.execute(
                            "INSERT INTO socksus_history "
                            "(account_id, old_pool_id, new_pool_id, reason) "
                            "VALUES (?, NULL, ?, 'purchase')",
                            (account_id, pool_id),
                        )
                        c.execute(
                            "INSERT INTO transaction_items "
                            "(transaction_id, nama_barang) VALUES (?, ?)",
                            (transaction_id, _conn_string(client_user, client_pass)),
                        )
                        blocks.append(
                            format_account_block(
                                index, client_user, client_pass, (host, port), expires_at
                            )
                        )
                    conn.commit()
                except ValueError as exc:
                    conn.rollback()
                    await interaction.followup.send(str(exc), ephemeral=True)
                    return
                except Exception as exc:
                    conn.rollback()
                    print(f"[SOCKSUS BUY] {exc}")
                    await interaction.followup.send(
                        "Transaksi gagal diproses. Silakan coba lagi.", ephemeral=True
                    )
                    return

            payload = "\n".join(blocks)
            try:
                await dm_channel.send(
                    content=(
                        f"✅ Pembelian **{quantity} {PRODUCT_TITLE}** berhasil.\n"
                        f"Konek ke `{SOCKSUS_HOST}:{SOCKSUS_PORT}` pakai username & "
                        f"password di file terlampir.\n"
                        f"IP exit kamu terkunci (lock). Gratis **Change IP "
                        f"{CHANGE_QUOTA}x** lewat tombol di panel — "
                        f"username & password tetap sama, cuma IP-nya yang ganti.\n"
                        f"Masa aktif sampai `{expires_at}` UTC."
                    ),
                    file=discord.File(
                        io.BytesIO(payload.encode("utf-8")),
                        filename=f"socks5-{transaction_id}.txt",
                    ),
                )
            except Exception:
                try:
                    rollback_delivery(user_id, account_ids, total, transaction_id)
                except Exception as exc:
                    print(f"[SOCKSUS BUY] Rollback gagal: {exc}")
                await interaction.followup.send(
                    "DM tidak dapat dikirim. Transaksi dibatalkan dan saldo dikembalikan.",
                    ephemeral=True,
                )
                return

            try:
                if ROLE_BUY and interaction.guild:
                    role = interaction.guild.get_role(ROLE_BUY)
                    if role:
                        await interaction.user.add_roles(role)
            except Exception as exc:
                print(f"[SOCKSUS BUY] Gagal memberi role customer: {exc}")

            await send_testimonial(interaction, transaction_id, quantity, total)
            await refresh_panel()

            balance_row = c.execute(
                "SELECT balance FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            balance = int(balance_row[0] or 0) if balance_row else 0
            await interaction.followup.send(
                f"Pembelian berhasil. Cek DM. Sisa saldo: {fmt_wl(balance)} WL.",
                ephemeral=True,
            )

    # --------------------------------------------------------------- change #
    def do_change_ip(account_id, user_id):
        """Tukar IP pool sebuah akun. Return (new_host, new_port, sisa_kuota).

        Kredensial klien tidak berubah - yang ditukar cuma upstream di belakangnya,
        jadi buyer tidak perlu setting ulang apa pun.
        """
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = c.execute(
                """
                SELECT pool_id, change_quota, change_used, last_change_at, locked, status
                FROM socksus_account
                WHERE id = ? AND CAST(buyer_user_id AS TEXT) = ?
                """,
                (account_id, str(user_id)),
            ).fetchone()
            if not row:
                raise ValueError("SOCKS itu bukan milikmu.")
            old_pool_id, quota, used, last_change_at, locked, status = row
            if status != "active":
                raise ValueError("SOCKS ini sudah tidak aktif.")
            if int(used or 0) >= int(quota or 0):
                raise ValueError(
                    f"Kuota Change IP habis ({used}/{quota}). Hubungi admin."
                )

            last_change = _parse_ts(last_change_at)
            if last_change is not None:
                remaining = CHANGE_COOLDOWN_SECONDS - int(
                    (_utcnow() - last_change).total_seconds()
                )
                if remaining > 0:
                    raise ValueError(f"Tunggu {remaining} detik sebelum ganti IP lagi.")

            new_pool = c.execute(
                """
                SELECT id, host, port FROM socksus_pool
                WHERE status = 'available' AND id != COALESCE(?, -1)
                ORDER BY COALESCE(last_used_at, added_at), id
                LIMIT 1
                """,
                (old_pool_id,),
            ).fetchone()
            if not new_pool:
                raise ValueError("Tidak ada IP pool kosong saat ini. Coba lagi nanti.")

            new_pool_id, new_host, new_port = new_pool
            if old_pool_id:
                c.execute(
                    "UPDATE socksus_pool SET status = 'available', "
                    "assigned_account_id = NULL WHERE id = ?",
                    (old_pool_id,),
                )
            c.execute(
                """
                UPDATE socksus_pool
                SET status = 'assigned', assigned_account_id = ?,
                    last_used_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'available'
                """,
                (account_id, new_pool_id),
            )
            if c.rowcount != 1:
                raise RuntimeError("IP pool keburu diambil. Coba lagi.")
            c.execute(
                "UPDATE socksus_account SET pool_id = ?, change_used = change_used + 1, "
                "last_change_at = ? WHERE id = ?",
                (new_pool_id, _iso(_utcnow()), account_id),
            )
            c.execute(
                "INSERT INTO socksus_history "
                "(account_id, old_pool_id, new_pool_id, reason) VALUES (?, ?, ?, 'change')",
                (account_id, old_pool_id, new_pool_id),
            )
            conn.commit()
            return new_host, new_port, int(quota) - int(used) - 1
        except Exception:
            conn.rollback()
            raise

    class SocksUsChangeSelect(Select):
        def __init__(self, user_id, rows):
            options = []
            for row in rows[:25]:
                account_id, client_user, host, port, used, quota = row
                status = "IP dedicated" if host else "belum ada IP"
                options.append(
                    discord.SelectOption(
                        label=f"#{account_id} · {client_user}"[:100],
                        description=f"{status} | change {used}/{quota}"[:100],
                        value=str(account_id),
                        emoji="🔄",
                    )
                )
            super().__init__(
                placeholder="Pilih SOCKS yang mau diganti IP-nya",
                min_values=1,
                max_values=1,
                options=options,
                custom_id=f"socksus_change_select_{user_id}",
            )
            self.user_id = user_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message(
                    "Pilihan ini bukan milikmu.", ephemeral=True
                )
                return

            account_id = int(self.values[0])
            async with _lock:
                try:
                    _new_host, _new_port, left = do_change_ip(account_id, self.user_id)
                except ValueError as exc:
                    await interaction.response.send_message(str(exc), ephemeral=True)
                    return
                except Exception as exc:
                    print(f"[SOCKSUS CHANGE] {exc}")
                    await interaction.response.send_message(
                        "Gagal ganti IP. Silakan coba lagi.", ephemeral=True
                    )
                    return

            row = c.execute(
                "SELECT client_user, client_pass FROM socksus_account WHERE id = ?",
                (account_id,),
            ).fetchone()
            conn_str = _conn_string(row[0], row[1]) if row else "-"
            await interaction.response.send_message(
                f"✅ IP SOCKS `#{account_id}` berhasil diganti ke IP baru (locked).\n"
                f"**Login tetap sama:** `{conn_str}`\n"
                f"Sisa kuota Change IP: `{left}x`\n"
                f"IP baru aktif dalam beberapa detik — koneksi lama silakan disconnect dulu.",
                ephemeral=True,
            )
            await refresh_panel()

    class SocksUsChangeView(View):
        def __init__(self, user_id, rows):
            super().__init__(timeout=90)
            self.add_item(SocksUsChangeSelect(user_id, rows))

    # ---------------------------------------------------------- interaksi UI #
    async def handle_account_button(interaction, custom_id):
        import ui_views

        user = interaction.user

        if custom_id == "socksus_growid":
            await interaction.response.send_modal(ui_views.GrowIDModal(user.id))
            return

        if custom_id == "socksus_balance":
            row = c.execute(
                "SELECT nama, balance FROM users WHERE user_id = ?", (user.id,)
            ).fetchone()
            if row:
                await ui_views.send_ephemeral_countdown(
                    interaction,
                    f"**GrowID:** `{row[0]}`\n"
                    f"**Balance:** `{fmt_wl(int(row[1] or 0))} WL`",
                )
            else:
                await ui_views.send_ephemeral_countdown(
                    interaction, "Kamu belum register. Klik Set GrowID dulu."
                )
            return

        if custom_id == "socksus_depo_qris":
            await interaction.response.send_modal(ui_views.DepoQRISModal(user))
            return

        if custom_id == "socksus_deposit":
            now_dep = time.time()
            last_dep = ui_views.DEPOSIT_COOLDOWNS.get(user.id, 0)
            if now_dep - last_dep < 40:
                remaining = int(40 - (now_dep - last_dep))
                await interaction.response.send_message(
                    f"Please wait {remaining} seconds before trying to deposit again.",
                    ephemeral=True,
                )
                return

            ui_views.DEPOSIT_COOLDOWNS[user.id] = now_dep

            if ui_views.is_getting_token:
                await interaction.response.send_message(
                    "Bot deposit sedang refresh token. Tunggu sebentar lalu coba lagi.",
                    ephemeral=True,
                )
                return
            if ui_views.is_deposit_active:
                await interaction.response.send_message(
                    "Deposit WL lain sedang berjalan. Coba lagi setelah selesai.",
                    ephemeral=True,
                )
                return

            ui_views.is_deposit_active = True
            await interaction.response.send_message(
                "Connecting deposit bot...", ephemeral=True
            )
            bot.loop.create_task(ui_views.run_deposit_session(interaction))

    @bot.listen("on_interaction")
    async def socksus_panel_interactions(interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = (getattr(interaction, "data", None) or {}).get("custom_id", "")
        if not custom_id.startswith("socksus_") or custom_id.startswith(
            "socksus_change_select_"
        ):
            return
        if interaction.response.is_done():
            return

        if custom_id in {
            "socksus_growid",
            "socksus_balance",
            "socksus_depo_qris",
            "socksus_deposit",
        }:
            await handle_account_button(interaction, custom_id)
            return

        if custom_id == "socksus_buy":
            await interaction.response.send_modal(SocksUsBuyModal())
            return

        if custom_id == "socksus_mine":
            rows = c.execute(
                """
                SELECT a.id, a.client_user, a.client_pass, p.host, p.port,
                       a.change_used, a.change_quota, a.expires_at
                FROM socksus_account a
                LEFT JOIN socksus_pool p ON p.id = a.pool_id
                WHERE CAST(a.buyer_user_id AS TEXT) = ? AND a.status = 'active'
                ORDER BY a.id DESC
                """,
                (str(interaction.user.id),),
            ).fetchall()
            if not rows:
                await interaction.response.send_message(
                    "Kamu belum punya SOCKS5 aktif.", ephemeral=True
                )
                return
            blocks = []
            for index, row in enumerate(rows, start=1):
                (
                    account_id,
                    client_user,
                    client_pass,
                    host,
                    port,
                    used,
                    quota,
                    expires_at,
                ) = row
                online, active = is_online(account_id)
                block = format_account_block(
                    account_id,
                    client_user,
                    client_pass,
                    (host, port) if host else None,
                    expires_at,
                )
                block += (
                    f"Change   : {used}/{quota} terpakai\n"
                    f"Status   : {'ONLINE (' + str(active) + ' koneksi)' if online else 'idle'}\n"
                )
                blocks.append(block)
            await interaction.response.send_message(
                f"Daftar SOCKS5 aktif kamu ({len(rows)}):",
                file=discord.File(
                    io.BytesIO("\n".join(blocks).encode("utf-8")),
                    filename="my-socks5.txt",
                ),
                ephemeral=True,
            )
            return

        if custom_id == "socksus_change":
            rows = c.execute(
                """
                SELECT a.id, a.client_user, p.host, p.port, a.change_used, a.change_quota
                FROM socksus_account a
                LEFT JOIN socksus_pool p ON p.id = a.pool_id
                WHERE CAST(a.buyer_user_id AS TEXT) = ? AND a.status = 'active'
                ORDER BY a.id DESC
                LIMIT 25
                """,
                (str(interaction.user.id),),
            ).fetchall()
            if not rows:
                await interaction.response.send_message(
                    "Kamu belum punya SOCKS5 aktif yang bisa diganti IP-nya.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                f"🔄 Pilih SOCKS yang mau ganti IP (gratis {CHANGE_QUOTA}x):",
                view=SocksUsChangeView(interaction.user.id, rows),
                ephemeral=True,
            )
            return

    # -------------------------------------------------------------- command #
    @bot.command(name="socksus", aliases=["socks5us"])
    @is_allowed_user()
    async def socksus_panel(ctx):
        """Pasang / pindahkan panel jualan SOCKS5 private ke channel ini."""
        await move_panel(ctx.channel)
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @bot.command(name="hargasocksus")
    @is_allowed_user()
    async def hargasocksus(ctx, jumlah: int = None):
        """Ubah harga produk SOCKS5 private (dalam WL)."""
        if jumlah is None:
            await ctx.reply(
                f"Format: `{PREFIX}hargasocksus <wl>` — contoh `{PREFIX}hargasocksus "
                f"{WL_PER_DL}` untuk 1 DL."
            )
            return
        if jumlah <= 0:
            await ctx.reply("Harga harus lebih dari 0.")
            return
        c.execute(
            "UPDATE stock SET harga = ? WHERE LOWER(kode) = ?", (jumlah, PRODUCT_CODE)
        )
        if c.rowcount == 0:
            c.execute(
                "INSERT INTO stock (kode, judul, harga) VALUES (?, ?, ?)",
                (PRODUCT_CODE, PRODUCT_TITLE, jumlah),
            )
        conn.commit()
        await refresh_panel()
        await ctx.reply(f"Harga SOCKS5 private diupdate jadi `{price_label(jumlah)}`.")

    @bot.command(name="pool")
    @is_allowed_user()
    async def pool_import(ctx, *, isi: str = None):
        """Import IP pool dari file .txt yang dilampirkan (atau teks di pesan).

        Format tiap baris: host:port:user:pass (juga menerima host:port dan
        user:pass@host:port).
        """
        raw = ""
        if ctx.message.attachments:
            for attachment in ctx.message.attachments:
                try:
                    data = await attachment.read()
                except Exception as exc:
                    await ctx.reply(f"Gagal baca `{attachment.filename}`: {exc}")
                    return
                raw += data.decode("utf-8", errors="replace") + "\n"
        elif isi:
            raw = isi.replace("```", "")
        else:
            await ctx.reply(
                f"Lampirkan file `.txt` berisi daftar proxy, lalu ketik `{PREFIX}pool`.\n"
                "Format per baris: `host:port:user:pass`"
            )
            return

        added = 0
        duplicate = 0
        invalid = []
        for lineno, line in enumerate(raw.splitlines(), start=1):
            if not line.strip() or line.strip().startswith(("#", "//")):
                continue
            parsed = parse_pool_line(line)
            if parsed is None:
                invalid.append(f"{lineno}: {line.strip()[:60]}")
                continue
            host, port, username, password = parsed
            proxy = f"{host}:{port}:{username}:{password}"
            try:
                c.execute(
                    "INSERT INTO socksus_pool (host, port, username, password, proxy) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (host, port, username, password, proxy),
                )
                added += 1
            except Exception:
                duplicate += 1
        conn.commit()

        available = c.execute(
            "SELECT COUNT(*) FROM socksus_pool WHERE status = 'available'"
        ).fetchone()[0]
        total = c.execute("SELECT COUNT(*) FROM socksus_pool").fetchone()[0]

        embed = discord.Embed(
            title="📥 Import IP Pool",
            color=discord.Color.green() if added else discord.Color.orange(),
        )
        embed.add_field(name="Ditambahkan", value=f"`{added}`", inline=True)
        embed.add_field(name="Duplikat (dilewati)", value=f"`{duplicate}`", inline=True)
        embed.add_field(name="Baris invalid", value=f"`{len(invalid)}`", inline=True)
        embed.add_field(name="Total pool", value=f"`{total}`", inline=True)
        embed.add_field(name="Siap dijual", value=f"`{available}`", inline=True)
        if invalid:
            embed.add_field(
                name="Contoh baris invalid",
                value="```" + "\n".join(invalid[:5])[:900] + "```",
                inline=False,
            )
        await ctx.reply(embed=embed)
        await refresh_panel()

    @bot.command(name="poollist")
    @is_allowed_user()
    async def pool_list(ctx, filter_arg: str = None):
        """Lihat IP pool dan user SOCKS yang terhubung ke tiap IP.

        `!poollist` semua · `!poollist used` cuma yang terpakai ·
        `!poollist free` cuma yang kosong · `!poollist dead` yang ditandai mati.
        """
        expire_due_accounts()

        where = ""
        params = []
        key = (filter_arg or "").lower()
        if key in {"used", "assigned", "terpakai"}:
            where = "WHERE p.status = 'assigned'"
        elif key in {"free", "available", "kosong"}:
            where = "WHERE p.status = 'available'"
        elif key in {"dead", "mati"}:
            where = "WHERE p.status = 'dead'"

        rows = c.execute(
            f"""
            SELECT p.id, p.host, p.port, p.username, p.password, p.status,
                   a.id, a.client_user, a.client_pass, a.buyer_user_id,
                   a.change_used, a.change_quota, a.locked, a.expires_at
            FROM socksus_pool p
            LEFT JOIN socksus_account a
                   ON a.id = p.assigned_account_id AND a.status = 'active'
            {where}
            ORDER BY p.id
            """,
            params,
        ).fetchall()

        if not rows:
            await ctx.reply("IP pool masih kosong. Import dulu dengan `!pool` + file .txt.")
            return

        lines = []
        for row in rows:
            (
                pool_id,
                host,
                port,
                up_user,
                up_pass,
                status,
                account_id,
                client_user,
                client_pass,
                buyer_id,
                used,
                quota,
                locked,
                expires_at,
            ) = row
            upstream = f"{host}:{port}"
            if up_user:
                upstream += f":{up_user}:{up_pass}"
            if account_id:
                online, active = is_online(account_id)
                lines.append(
                    f"#{pool_id:<4} {status.upper():<9} {upstream}\n"
                    f"      -> user  : {_conn_string(client_user, client_pass)}\n"
                    f"      -> buyer : {buyer_id} | akun #{account_id}\n"
                    f"      -> change: {used}/{quota} | exp {expires_at}"
                    f" | {'ONLINE (' + str(active) + ' koneksi)' if online else 'idle'}"
                )
            else:
                lines.append(f"#{pool_id:<4} {status.upper():<9} {upstream}")

        available = sum(1 for r in rows if r[5] == "available")
        assigned = sum(1 for r in rows if r[5] == "assigned")
        dead = sum(1 for r in rows if r[5] == "dead")
        header = (
            f"IP POOL — {SOCKSUS_HOST}:{SOCKSUS_PORT}\n"
            f"total {len(rows)} | kosong {available} | terpakai {assigned} | mati {dead}\n"
            + "=" * 66
        )
        body = header + "\n" + "\n".join(lines)

        if len(body) <= 1900 and len(rows) <= 15:
            await ctx.reply(f"```\n{body}\n```")
        else:
            await ctx.reply(
                f"IP pool: **{len(rows)}** entri "
                f"(kosong `{available}` · terpakai `{assigned}` · mati `{dead}`)",
                file=discord.File(
                    io.BytesIO(body.encode("utf-8")), filename="poollist.txt"
                ),
            )

    @bot.command(name="pooldel")
    @is_allowed_user()
    async def pool_delete(ctx, target: str = None):
        """Hapus 1 IP dari pool: `!pooldel <id>` atau `!pooldel <host>`."""
        if not target:
            await ctx.reply(f"Format: `{PREFIX}pooldel <id|host>`")
            return
        if target.isdigit():
            rows = c.execute(
                "SELECT id, proxy, status, assigned_account_id FROM socksus_pool "
                "WHERE id = ?",
                (int(target),),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, proxy, status, assigned_account_id FROM socksus_pool "
                "WHERE host = ?",
                (target,),
            ).fetchall()
        if not rows:
            await ctx.reply("IP itu tidak ada di pool.")
            return

        busy = [str(r[0]) for r in rows if r[2] == "assigned"]
        if busy:
            await ctx.reply(
                f"IP `#{', #'.join(busy)}` masih dipakai buyer. "
                f"Tandai mati dulu dengan `{PREFIX}pooldead <id>`, "
                "atau pindahkan buyer-nya lewat Change IP."
            )
            return
        c.execute(
            f"DELETE FROM socksus_pool WHERE id IN ({','.join('?' * len(rows))})",
            [r[0] for r in rows],
        )
        conn.commit()
        await refresh_panel()
        await ctx.reply(f"Dihapus `{len(rows)}` IP dari pool.")

    @bot.command(name="pooldead")
    @is_allowed_user()
    async def pool_dead(ctx, pool_id: int = None):
        """Tandai IP pool sebagai mati supaya tidak ikut dijual lagi."""
        if pool_id is None:
            await ctx.reply(f"Format: `{PREFIX}pooldead <id>`")
            return
        row = c.execute(
            "SELECT status, assigned_account_id FROM socksus_pool WHERE id = ?",
            (pool_id,),
        ).fetchone()
        if not row:
            await ctx.reply("IP itu tidak ada di pool.")
            return
        c.execute(
            "UPDATE socksus_pool SET status = 'dead', assigned_account_id = NULL "
            "WHERE id = ?",
            (pool_id,),
        )
        if row[1]:
            c.execute(
                "UPDATE socksus_account SET pool_id = NULL WHERE id = ?", (row[1],)
            )
        conn.commit()
        await refresh_panel()
        note = ""
        if row[1]:
            note = (
                f" Akun `#{row[1]}` sekarang tanpa IP — suruh buyer klik "
                "**Change IP** untuk dapat pengganti."
            )
        await ctx.reply(f"IP `#{pool_id}` ditandai mati.{note}")

    @bot.command(name="poolrevive")
    @is_allowed_user()
    async def pool_revive(ctx, pool_id: int = None):
        """Kembalikan IP yang ditandai mati ke stok."""
        if pool_id is None:
            await ctx.reply(f"Format: `{PREFIX}poolrevive <id>`")
            return
        c.execute(
            "UPDATE socksus_pool SET status = 'available', assigned_account_id = NULL "
            "WHERE id = ? AND status = 'dead'",
            (pool_id,),
        )
        conn.commit()
        if c.rowcount == 0:
            await ctx.reply("IP itu tidak ada atau statusnya bukan `dead`.")
            return
        await refresh_panel()
        await ctx.reply(f"IP `#{pool_id}` dikembalikan ke stok.")

    @bot.command(name="socksquota")
    @is_allowed_user()
    async def socks_quota(ctx, account_id: int = None, jumlah: int = None):
        """Set ulang kuota Change IP sebuah akun."""
        if account_id is None or jumlah is None:
            await ctx.reply(f"Format: `{PREFIX}socksquota <id_akun> <jumlah>`")
            return
        c.execute(
            "UPDATE socksus_account SET change_quota = ? WHERE id = ?",
            (max(0, jumlah), account_id),
        )
        conn.commit()
        if c.rowcount == 0:
            await ctx.reply("Akun itu tidak ada.")
            return
        await ctx.reply(f"Kuota Change IP akun `#{account_id}` diset jadi `{jumlah}x`.")

    # ----------------------------------------------------------------- loop #
    @tasks.loop(minutes=max(1, PANEL_UPDATE_MINUTES))
    async def update_socksus_panel():
        try:
            expired = expire_due_accounts()
            if expired:
                print(f"[SOCKSUS] {expired} akun kadaluarsa, IP-nya dilepas ke stok.")
        except Exception as exc:
            print(f"[SOCKSUS] Gagal proses kadaluarsa: {exc}")
        await refresh_panel()

    @update_socksus_panel.before_loop
    async def before_update_socksus_panel():
        await bot.wait_until_ready()

    @bot.listen("on_ready")
    async def initialize_socksus_panel():
        nonlocal ready_initialized
        if ready_initialized:
            return
        ready_initialized = True

        # View persisten: tombol panel lama tetap hidup setelah bot restart.
        bot.add_view(SocksUsStoreView())

        row = c.execute(
            "SELECT channel_id, message_id FROM socksus_panel_settings WHERE id = 1"
        ).fetchone()
        if row:
            panel_cache["channel_id"], panel_cache["message_id"] = row
        if not panel_cache["channel_id"] and PANEL_CHANNEL_ID:
            panel_cache["channel_id"] = PANEL_CHANNEL_ID

        # Panel langsung disegarkan/dipasang ulang tanpa perlu !socksus lagi.
        await refresh_panel()

        if not update_socksus_panel.is_running():
            update_socksus_panel.start()
            print(
                f"[SOCKSUS] Panel auto-update tiap {PANEL_UPDATE_MINUTES} menit aktif."
            )
