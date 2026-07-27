import asyncio
import datetime
import io
import json
import os
import time
from pathlib import Path

import discord
from discord.ui import Button, Modal, Select, TextInput, View
from discord.ext import tasks

from utils import is_allowed_user


PRODUCT_CODE = "socks"
PRODUCT_TITLE = "Socks Weekly 🇵🇭"
DEFAULT_PRICE_WL = int(os.getenv("SOCKS_PRICE_WL", "500"))
RESET_COOLDOWN_SECONDS = int(os.getenv("SOCKS_RESET_COOLDOWN_SECONDS", "300"))
CHANNEL_TESTIMONI = int(os.getenv("CHANNEL_TESTIMONI", "0") or 0)
ROLE_BUY = int(os.getenv("ROLE_BUY", "0") or 0)
ROOT_DIR = Path(__file__).resolve().parent.parent
PA_DIR = ROOT_DIR / "pa"
RESET_WORKER = PA_DIR / "src" / "workers" / "resetSocksIp.js"

_buy_lock = asyncio.Lock()
_reset_tasks = set()

EMO_EXCLAMATION = "<a:exclamation:1419518587072282654>"
EMO_PRODUCT = "<a:toa:1122531485090582619>"
EMO_ARROW = "<a:panah1:1419515217892606053>"
EMO_WL = "<a:world_lock:1419515667773657109>"


def _btn_emoji(name, emoji_id, animated=False):
    return discord.PartialEmoji(name=name, id=emoji_id, animated=animated)


def _ensure_schema(c, conn):
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS socks_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_item_id INTEGER,
            proxy TEXT NOT NULL UNIQUE,
            ip TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 1080,
            socks_user TEXT NOT NULL,
            socks_password TEXT NOT NULL,
            account_label TEXT NOT NULL,
            region TEXT NOT NULL,
            server_uuid TEXT NOT NULL UNIQUE,
            server_name TEXT,
            status TEXT NOT NULL DEFAULT 'available',
            buyer_user_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sold_at TEXT,
            last_reset_at TEXT,
            reset_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS socks_ip_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inventory_id INTEGER NOT NULL,
            old_ip TEXT,
            new_ip TEXT,
            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reason TEXT NOT NULL DEFAULT 'reset'
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS socks_panel_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            channel_id INTEGER,
            message_id INTEGER
        )
        """
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_socks_inventory_status "
        "ON socks_inventory(status)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_socks_inventory_buyer "
        "ON socks_inventory(buyer_user_id, status)"
    )
    c.execute(
        "INSERT OR IGNORE INTO stock (kode, judul, harga) VALUES (?, ?, ?)",
        (PRODUCT_CODE, PRODUCT_TITLE, DEFAULT_PRICE_WL),
    )
    c.execute(
        "INSERT OR IGNORE INTO socks_panel_settings (id, channel_id, message_id) "
        "VALUES (1, NULL, NULL)"
    )
    conn.commit()


class SocksStoreView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            Button(
                label="Buy SOCKS",
                emoji=_btn_emoji("toa", 1122531485090582619, True),
                style=discord.ButtonStyle.green,
                custom_id="socks_buy",
            )
        )
        self.add_item(
            Button(
                label="Reset IP",
                emoji=_btn_emoji("panah1", 1419515217892606053, True),
                style=discord.ButtonStyle.danger,
                custom_id="socks_reset",
            )
        )
        self.add_item(
            Button(
                label="My SOCKS",
                emoji=_btn_emoji("toa", 1122531485090582619, True),
                style=discord.ButtonStyle.secondary,
                custom_id="socks_mine",
            )
        )
        self.add_item(
            Button(
                label="Depo QRIS",
                emoji=_btn_emoji("world_lock", 1419515667773657109, True),
                style=discord.ButtonStyle.blurple,
                custom_id="socks_depo_qris",
            )
        )
        self.add_item(
            Button(
                label="Deposit WL",
                emoji=_btn_emoji("world_lock", 1419515667773657109, True),
                style=discord.ButtonStyle.blurple,
                custom_id="socks_deposit",
            )
        )
        self.add_item(
            Button(
                label="Set GrowID",
                emoji=_btn_emoji("toa", 1122531485090582619, True),
                style=discord.ButtonStyle.gray,
                custom_id="socks_growid",
            )
        )
        self.add_item(
            Button(
                label="My Balance",
                emoji=_btn_emoji("world_lock", 1419515667773657109, True),
                style=discord.ButtonStyle.secondary,
                custom_id="socks_balance",
            )
        )


def setup(bot, c, conn, fmt_wl, PREFIX):
    _ensure_schema(c, conn)
    panel_cache = {"channel_id": None, "message_id": None, "message": None}
    ready_initialized = False

    def get_price():
        row = c.execute(
            "SELECT harga FROM stock WHERE kode = ?", (PRODUCT_CODE,)
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def build_panel_embed():
        available = c.execute(
            "SELECT COUNT(*) FROM socks_inventory WHERE status = 'available'"
        ).fetchone()[0]
        sold = c.execute(
            """
            SELECT COALESCE(SUM(jumlah), 0)
            FROM transactions
            WHERE LOWER(kode) = LOWER(?)
            """,
            (PRODUCT_CODE,),
        ).fetchone()[0]
        resetting = c.execute(
            "SELECT COUNT(*) FROM socks_inventory WHERE status = 'resetting'"
        ).fetchone()[0]
        inactive = c.execute(
            "SELECT COUNT(*) FROM socks_inventory WHERE status = 'inactive'"
        ).fetchone()[0]
        price = get_price()

        lines = [
            f"{EMO_PRODUCT}  **{PRODUCT_TITLE}** (`SOCKS`)",
            f"{EMO_ARROW}  **Stock:** `{available}`",
            f"{EMO_ARROW}  **Price:** `{fmt_wl(price)}` {EMO_WL}",
            f"{EMO_ARROW}  **Active:** `5 Days`",
            f"{EMO_ARROW}  **Product Sold:** `{sold}`",
        ]
        if resetting:
            lines.append(f"{EMO_ARROW}  **Reset Process:** `{resetting}`")
        if inactive:
            lines.append(f"{EMO_ARROW}  **Inactive:** `{inactive}`")

        embed = discord.Embed(
            title=f"{EMO_EXCLAMATION} SOCKS5 LIST {EMO_EXCLAMATION}",
            description="\n".join(lines),
            color=discord.Color.red(),
        )
        embed.set_footer(
            text=f" Last Update: {datetime.datetime.now().strftime('%H:%M:%S')}"
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

    async def save_panel(channel_id, message_id, message=None):
        panel_cache.update(
            {
                "channel_id": channel_id,
                "message_id": message_id,
                "message": message,
            }
        )
        c.execute(
            "UPDATE socks_panel_settings SET channel_id = ?, message_id = ? "
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

        message = await channel.send(embed=build_panel_embed(), view=SocksStoreView())
        await save_panel(channel.id, message.id, message)
        return message

    async def send_socks_testimonial(interaction, transaction_id, quantity, total):
        if not CHANNEL_TESTIMONI:
            return

        channel = bot.get_channel(CHANNEL_TESTIMONI)
        if channel is None:
            try:
                channel = await bot.fetch_channel(CHANNEL_TESTIMONI)
            except Exception as exc:
                print(f"[SOCKS TESTIMONI] Gagal fetch channel: {exc}")
                return

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
            name="Total Price",
            value=f"{fmt_wl(total)} {EMO_WL}",
            inline=False,
        )
        embed.set_footer(text="Thanks For Purchasing Our Product(s)")

        try:
            await channel.send(embed=embed)
        except Exception as exc:
            print(f"[SOCKS TESTIMONI] Gagal kirim testimoni: {exc}")

    async def compensate_failed_delivery(user_id, inventory_ids, total, tx_id):
        async with _buy_lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                c.execute(
                    "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                    (total, user_id),
                )
                for inventory_id in inventory_ids:
                    row = c.execute(
                        "SELECT proxy FROM socks_inventory WHERE id = ? "
                        "AND status = 'sold' AND CAST(buyer_user_id AS TEXT) = ?",
                        (inventory_id, str(user_id)),
                    ).fetchone()
                    if not row:
                        continue
                    stock_result = c.execute(
                        "INSERT INTO stock_items (kode, nama_barang) VALUES (?, ?)",
                        (PRODUCT_CODE, row[0]),
                    )
                    c.execute(
                        """
                        UPDATE socks_inventory
                        SET status = 'available', buyer_user_id = NULL,
                            sold_at = NULL, stock_item_id = ?
                        WHERE id = ?
                        """,
                        (stock_result.lastrowid, inventory_id),
                    )
                c.execute("DELETE FROM transaction_items WHERE transaction_id = ?", (tx_id,))
                c.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    class SocksBuyModal(Modal, title="Buy SOCKS5"):
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
                if quantity < 1 or quantity > 10:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    "Jumlah harus 1 sampai 10.", ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True)
            user_id = interaction.user.id
            try:
                dm_channel = await interaction.user.create_dm()
            except Exception:
                await interaction.followup.send(
                    "DM tidak dapat dibuka. Aktifkan DM lalu coba lagi.",
                    ephemeral=True,
                )
                return
            inventory_ids = []
            proxies = []
            transaction_id = None
            total = 0

            async with _buy_lock:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    user_row = c.execute(
                        "SELECT nama, balance FROM users WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()
                    if not user_row:
                        raise ValueError("Klik Set GrowID terlebih dahulu.")

                    price = get_price()
                    if price <= 0:
                        raise ValueError(
                            "Harga SOCKS belum diatur admin. Gunakan !setharga socks <harga>."
                        )
                    total = price * quantity
                    if int(user_row[1] or 0) < total:
                        raise ValueError(
                            f"Saldo kurang. Dibutuhkan {fmt_wl(total)} WL."
                        )

                    items = c.execute(
                        """
                        SELECT id, stock_item_id, proxy
                        FROM socks_inventory
                        WHERE status = 'available'
                        ORDER BY id
                        LIMIT ?
                        """,
                        (quantity,),
                    ).fetchall()
                    if len(items) < quantity:
                        raise ValueError("Stock SOCKS tidak cukup.")

                    inventory_ids = [int(item[0]) for item in items]
                    proxies = [item[2] for item in items]
                    stock_item_ids = [int(item[1]) for item in items if item[1]]

                    placeholders = ",".join("?" for _ in inventory_ids)
                    c.execute(
                        f"""
                        UPDATE socks_inventory
                        SET status = 'sold', buyer_user_id = ?,
                            sold_at = CURRENT_TIMESTAMP, stock_item_id = NULL
                        WHERE id IN ({placeholders}) AND status = 'available'
                        """,
                        (user_id, *inventory_ids),
                    )
                    if c.rowcount != quantity:
                        raise RuntimeError("Stock berubah saat transaksi. Silakan coba lagi.")

                    if stock_item_ids:
                        stock_placeholders = ",".join("?" for _ in stock_item_ids)
                        c.execute(
                            f"DELETE FROM stock_items WHERE id IN ({stock_placeholders})",
                            stock_item_ids,
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
                    for proxy in proxies:
                        c.execute(
                            "INSERT INTO transaction_items "
                            "(transaction_id, nama_barang) VALUES (?, ?)",
                            (transaction_id, proxy),
                        )
                    conn.commit()
                except ValueError as exc:
                    conn.rollback()
                    await interaction.followup.send(str(exc), ephemeral=True)
                    return
                except Exception as exc:
                    conn.rollback()
                    print(f"[SOCKS BUY] {exc}")
                    await interaction.followup.send(
                        "Transaksi gagal diproses. Silakan coba lagi.", ephemeral=True
                    )
                    return

            payload = "\n".join(proxies)
            try:
                await dm_channel.send(
                    content=(
                        f"Pembelian {quantity} SOCKS5 berhasil. "
                        "Gunakan tombol Reset IP pada panel bila diperlukan."
                    ),
                    file=discord.File(
                        io.BytesIO(payload.encode("utf-8")),
                        filename=f"socks-{transaction_id}.txt",
                    ),
                )
            except Exception:
                await compensate_failed_delivery(
                    user_id, inventory_ids, total, transaction_id
                )
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
                    else:
                        print("[SOCKS BUY] Role customer tidak ditemukan.")
            except Exception as exc:
                print(f"[SOCKS BUY] Gagal memberi role customer: {exc}")

            await send_socks_testimonial(
                interaction, transaction_id, quantity, total
            )

            balance = c.execute(
                "SELECT balance FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            await interaction.followup.send(
                f"Pembelian berhasil. Cek DM. Sisa saldo: {fmt_wl(balance)} WL.",
                ephemeral=True,
            )

    class SocksResetSelect(Select):
        def __init__(self, user_id, rows):
            options = []
            for row in rows[:25]:
                inventory_id, proxy, ip, region, reset_count = row
                options.append(
                    discord.SelectOption(
                        label=f"#{inventory_id} - {ip}"[:100],
                        description=(
                            f"{proxy} | {region} | reset {reset_count}x"
                        )[:100],
                        value=str(inventory_id),
                        emoji="🔄",
                    )
                )
            super().__init__(
                placeholder="Pilih proxy yang mau di-reset IP",
                min_values=1,
                max_values=1,
                options=options,
                custom_id=f"socks_reset_select_{user_id}",
            )
            self.user_id = user_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message(
                    "Pilihan ini bukan milikmu.", ephemeral=True
                )
                return

            inventory_id = int(self.values[0])
            now = datetime.datetime.now(datetime.timezone.utc)
            async with _buy_lock:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    row = c.execute(
                        """
                        SELECT last_reset_at
                        FROM socks_inventory
                        WHERE id = ? AND CAST(buyer_user_id AS TEXT) = ? AND status = 'sold'
                        """,
                        (inventory_id, str(self.user_id)),
                    ).fetchone()
                    if not row:
                        raise ValueError("SOCKS tidak tersedia untuk di-reset.")
                    if row[0]:
                        last_reset = datetime.datetime.fromisoformat(
                            str(row[0]).replace("Z", "+00:00")
                        )
                        if last_reset.tzinfo is None:
                            last_reset = last_reset.replace(
                                tzinfo=datetime.timezone.utc
                            )
                        remaining = RESET_COOLDOWN_SECONDS - int(
                            (now - last_reset).total_seconds()
                        )
                        if remaining > 0:
                            raise ValueError(
                                f"Reset berikutnya tersedia dalam {remaining} detik."
                            )
                    c.execute(
                        "UPDATE socks_inventory SET status = 'resetting', "
                        "last_error = NULL WHERE id = ?",
                        (inventory_id,),
                    )
                    conn.commit()
                except ValueError as exc:
                    conn.rollback()
                    await interaction.response.send_message(
                        str(exc), ephemeral=True
                    )
                    return
                except Exception:
                    conn.rollback()
                    await interaction.response.send_message(
                        "Gagal membuat antrean reset.", ephemeral=True
                    )
                    return

            await interaction.response.send_message(
                "🔄 Reset IP dimulai. Bot akan nunggu sampai dapat IP baru, lalu proxy baru dikirim ke DM.",
                ephemeral=True,
            )
            task = asyncio.create_task(run_reset_worker(inventory_id, self.user_id))
            _reset_tasks.add(task)
            task.add_done_callback(_reset_tasks.discard)

    class SocksResetView(View):
        def __init__(self, user_id, rows):
            super().__init__(timeout=90)
            self.add_item(SocksResetSelect(user_id, rows))

    async def run_reset_worker(inventory_id, user_id):
        try:
            process = await asyncio.create_subprocess_exec(
                "node",
                str(RESET_WORKER),
                str(inventory_id),
                cwd=str(PA_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await process.communicate()
            output = stdout.decode("utf-8", errors="replace")
            marker = "SOCKS_RESET_RESULT="
            result = None
            for line in reversed(output.splitlines()):
                if line.startswith(marker):
                    result = json.loads(line[len(marker) :])
                    break
            if not result:
                result = {"ok": False, "error": "Worker tidak memberi hasil valid."}
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
            c.execute(
                "UPDATE socks_inventory SET status = 'sold', last_error = ? "
                "WHERE id = ? AND status = 'resetting'",
                (str(exc)[:1000], inventory_id),
            )
            conn.commit()

        try:
            user = await bot.fetch_user(user_id)
            if result.get("ok"):
                await user.send(
                    "✅ Reset IP berhasil. Proxy baru:",
                    file=discord.File(
                        io.BytesIO(result["proxy"].encode("utf-8")),
                        filename=f"socks-reset-{inventory_id}.txt",
                    ),
                )
            else:
                error_text = result.get("error", "Unknown error")
                await user.send(
                    f"❌ Reset IP SOCKS5 #{inventory_id} gagal: "
                    f"{error_text}"
                )
        except Exception as exc:
            print(f"[SOCKS RESET] Gagal DM user {user_id}: {exc}")

    async def handle_socks_account_button(interaction, custom_id):
        import ui_views

        user = interaction.user

        if custom_id == "socks_growid":
            await interaction.response.send_modal(ui_views.GrowIDModal(user.id))
            return True

        if custom_id == "socks_balance":
            c.execute("SELECT nama, balance FROM users WHERE user_id=?", (user.id,))
            row = c.fetchone()
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
            return True

        if custom_id == "socks_depo_qris":
            await interaction.response.send_modal(ui_views.DepoQRISModal(user))
            return True

        if custom_id == "socks_deposit":
            now_dep = time.time()
            last_dep = ui_views.DEPOSIT_COOLDOWNS.get(user.id, 0)
            if now_dep - last_dep < 40:
                remaining = int(40 - (now_dep - last_dep))
                await interaction.response.send_message(
                    f"Please wait {remaining} seconds before trying to deposit again.",
                    ephemeral=True,
                )
                return True

            ui_views.DEPOSIT_COOLDOWNS[user.id] = now_dep

            if ui_views.is_getting_token:
                await interaction.response.send_message(
                    "Bot deposit sedang refresh token. Tunggu sebentar lalu coba lagi.",
                    ephemeral=True,
                )
                return True

            if ui_views.is_deposit_active:
                await interaction.response.send_message(
                    "Deposit WL lain sedang berjalan. Coba lagi setelah selesai.",
                    ephemeral=True,
                )
                return True

            ui_views.is_deposit_active = True
            await interaction.response.send_message(
                "Connecting deposit bot...", ephemeral=True
            )
            bot.loop.create_task(ui_views.run_deposit_session(interaction))
            return True

        return False

    @bot.listen("on_interaction")
    async def socks_panel_interactions(interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = (getattr(interaction, "data", None) or {}).get("custom_id", "")
        if custom_id in {
            "socks_growid",
            "socks_balance",
            "socks_depo_qris",
            "socks_deposit",
        }:
            if not interaction.response.is_done():
                await handle_socks_account_button(interaction, custom_id)
            return
        if custom_id == "socks_buy":
            if not interaction.response.is_done():
                await interaction.response.send_modal(SocksBuyModal())
            return
        if custom_id == "socks_mine":
            rows = c.execute(
                """
                SELECT proxy
                FROM socks_inventory
                WHERE CAST(buyer_user_id AS TEXT) = ?
                  AND status IN ('sold', 'resetting')
                ORDER BY id DESC
                """,
                (str(interaction.user.id),),
            ).fetchall()
            if not rows:
                await interaction.response.send_message(
                    "Kamu belum memiliki SOCKS aktif.", ephemeral=True
                )
                return
            lines = [row[0] for row in rows]
            await interaction.response.send_message(
                "Daftar SOCKS aktif:",
                file=discord.File(
                    io.BytesIO("\n".join(lines).encode("utf-8")),
                    filename="my-socks.txt",
                ),
                ephemeral=True,
            )
            return
        if custom_id != "socks_reset" or interaction.response.is_done():
            return

        rows = c.execute(
            """
            SELECT id, proxy, ip, region, reset_count
            FROM socks_inventory
            WHERE CAST(buyer_user_id AS TEXT) = ? AND status = 'sold'
            ORDER BY id DESC
            LIMIT 25
            """,
            (str(interaction.user.id),),
        ).fetchall()
        if not rows:
            await interaction.response.send_message(
                "Kamu belum memiliki SOCKS aktif yang bisa di-reset.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "🔄 Pilih proxy yang ingin di-reset IP:",
            view=SocksResetView(interaction.user.id, rows),
            ephemeral=True,
        )

    @bot.command(name="socks5", aliases=["socks"])
    @is_allowed_user()
    async def socks_panel(ctx):
        """Pasang atau pindahkan panel jualan SOCKS ke channel ini."""
        await move_panel(ctx.channel)
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @bot.command(name="hargasocks")
    @is_allowed_user()
    async def hargasocks(ctx, jumlah: int = None):
        """Update harga produk SOCKS."""
        if jumlah is None:
            await ctx.reply(f"Format: `{PREFIX}hargasocks <jumlah>`")
            return
        if jumlah <= 0:
            await ctx.reply("Harga SOCKS harus lebih dari 0.")
            return

        c.execute(
            "UPDATE stock SET harga = ? WHERE LOWER(kode) = ?",
            (jumlah, PRODUCT_CODE),
        )
        if c.rowcount == 0:
            c.execute(
                "INSERT INTO stock (kode, judul, harga) VALUES (?, ?, ?)",
                (PRODUCT_CODE, PRODUCT_TITLE, jumlah),
            )
        conn.commit()

        message = await fetch_panel_message()
        if message is not None:
            try:
                await message.edit(embed=build_panel_embed(), view=SocksStoreView())
            except Exception as exc:
                print(f"[SOCKS PANEL] Gagal refresh harga: {exc}")
                panel_cache["message"] = None

        await ctx.reply(f"Harga SOCKS berhasil diupdate menjadi `{fmt_wl(jumlah)}` WL.")

    @tasks.loop(seconds=10)
    async def update_socks_panel():
        message = await fetch_panel_message()
        if message is None:
            return
        try:
            await message.edit(embed=build_panel_embed(), view=SocksStoreView())
        except Exception as exc:
            print(f"[SOCKS PANEL] Gagal update: {exc}")
            panel_cache["message"] = None

    @bot.listen("on_ready")
    async def initialize_socks_panel():
        nonlocal ready_initialized
        if ready_initialized:
            return
        ready_initialized = True
        bot.add_view(SocksStoreView())
        row = c.execute(
            "SELECT channel_id, message_id FROM socks_panel_settings WHERE id = 1"
        ).fetchone()
        if row:
            panel_cache["channel_id"], panel_cache["message_id"] = row
        c.execute(
            """
            UPDATE socks_inventory
            SET status = 'sold',
                last_error = COALESCE(last_error, 'Reset terputus saat bot restart')
            WHERE status = 'resetting'
            """
        )
        conn.commit()
        if not update_socks_panel.is_running():
            update_socks_panel.start()

    @update_socks_panel.before_loop
    async def before_update_socks_panel():
        await bot.wait_until_ready()
