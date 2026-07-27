import asyncio
import datetime
import os

import discord
from discord import app_commands
from discord.ext import tasks
from discord.ui import Button, Modal, Select, TextInput, View

from utils import is_allowed_user, is_maintenance


WL_EMOJI = "<a:world_lock:1419515667773657109>"
SERVICE_PRICE = 35

SERVICE_TYPES = {
    "plant": {
        "label": "PLANT SERVICE",
        "emoji": "🌱",
        "code": "plant"
    },
    "harvest": {
        "label": "HARVEST SERVICE",
        "emoji": "🚜",
        "code": "ht"
    },
    "harvest_move": {
        "label": "HARVEST + MOVE",
        "emoji": "🚛",
        "code": "htmove"
    },
    "move_block_seed": {
        "label": "MOVE BLOCK/SEED",
        "emoji": "📦",
        "code": "move"
    },
    "splice_seed": {
        "label": "SPLICE SEED",
        "emoji": "🧪",
        "code": "splice"
    },
}


def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register the paid Plant/Harvest service menu and its admin workflow."""
    message_cache = {"channel_id": None, "message": None}
    startup_initialized = False
    order_lock = asyncio.Lock()
    server_id_raw = os.getenv("SERVER_ID", "").strip()
    env_channel_id = int(os.getenv("SERVICE_CHANNEL_ID", "0") or 0)
    order_channel_id = int(os.getenv("SERVICE_ORDER_CHANNEL_ID", "0") or 0)

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS service_prices (
            code TEXT PRIMARY KEY,
            price INTEGER DEFAULT 35
        )
        """
    )
    # Initialize default prices
    for config in SERVICE_TYPES.values():
        c.execute("INSERT OR IGNORE INTO service_prices (code, price) VALUES (?, 35)", (config["code"],))
    conn.commit()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS service_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            service_type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            storage TEXT NOT NULL,
            iddoor TEXT NOT NULL,
            worlds TEXT NOT NULL,
            item TEXT NOT NULL,
            price INTEGER DEFAULT 35,
            total INTEGER DEFAULT 0,
            growid TEXT,
            status TEXT DEFAULT 'pending',
            cancel_reason TEXT,
            message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    columns = {
        row[1] for row in c.execute("PRAGMA table_info(service_orders)").fetchall()
    }
    migrations = {
        "price": "ALTER TABLE service_orders ADD COLUMN price INTEGER DEFAULT 35",
        "total": "ALTER TABLE service_orders ADD COLUMN total INTEGER DEFAULT 0",
        "growid": "ALTER TABLE service_orders ADD COLUMN growid TEXT",
        "cancel_reason": "ALTER TABLE service_orders ADD COLUMN cancel_reason TEXT",
        "message_id": "ALTER TABLE service_orders ADD COLUMN message_id INTEGER",
    }
    for column, query in migrations.items():
        if column not in columns:
            c.execute(query)
    conn.commit()

    def get_saved_channel_id():
        if env_channel_id:
            return env_channel_id
        c.execute("SELECT value FROM bot_settings WHERE key='service_channel_id'")
        row = c.fetchone()
        try:
            return int(row[0]) if row else 0
        except (TypeError, ValueError):
            return 0

    def get_service_price(service_type):
        code = SERVICE_TYPES.get(service_type, {}).get("code")
        if not code:
            return 35
        c.execute("SELECT price FROM service_prices WHERE code=?", (code,))
        row = c.fetchone()
        return int(row[0]) if row else 35

    def save_channel_id(channel_id):
        c.execute(
            """
            INSERT INTO bot_settings (key, value)
            VALUES ('service_channel_id', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str(channel_id),),
        )
        conn.commit()

    def build_embed():
        parts = []
        for service_type, config in SERVICE_TYPES.items():
            price = get_service_price(service_type)
            unit = "1k Block" if service_type == "move_block_seed" else "World"
            parts.append(
                f"{config['emoji']}  **{config['label']}** (`{config['code']}`)\n"
                f"<a:panah1:1419515217892606053>  "
                f"**Price:** `{fmt_wl(price)}` {WL_EMOJI} per {unit}"
            )

        embed = discord.Embed(
            title=(
                "<a:exclamation:1419518587072282654> SERVICE LIST "
                "<a:exclamation:1419518587072282654>"
            ),
            description=("\n" + ("=" * 28) + "\n").join(parts),
            color=discord.Color.red(),
        )
        embed.add_field(
            name="Cara Order",
            value="Pilih service melalui dropdown di bawah lalu isi formulir.",
            inline=False,
        )
        embed.set_footer(
            text=f"Last Update: {datetime.datetime.now().strftime('%H:%M:%S')}"
        )
        return embed

    def parse_world_names(raw_value):
        return [name.strip() for name in raw_value.split(",") if name.strip()]

    def status_label(status):
        return {
            "pending": "🟡 PENDING",
            "processing": "🔵 LOADING / DIPROSES",
            "cancelled": "🔴 CANCELLED",
            "done": "🟢 DONE",
        }.get(status, status.upper())

    def build_order_embed(row):
        (
            order_id,
            user_id,
            service_type,
            amount,
            storage,
            iddoor,
            worlds,
            item,
            price,
            total,
            growid,
            status,
            cancel_reason,
        ) = row
        config = SERVICE_TYPES.get(service_type, SERVICE_TYPES["plant"])
        color = {
            "pending": discord.Color.gold(),
            "processing": discord.Color.blue(),
            "cancelled": discord.Color.red(),
            "done": discord.Color.green(),
        }.get(status, discord.Color.light_grey())

        embed = discord.Embed(
            title=f"Service Order #{order_id}",
            description=(
                f"{config['emoji']} **{config['label']}**\n"
                f"Status: **{status_label(status)}**"
            ),
            color=color,
        )
        embed.add_field(
            name="Pembeli",
            value=f"<@{user_id}> (`{growid or '-'}`)",
            inline=False,
        )

        if service_type == "move_block_seed":
            embed.add_field(name="Total Block", value=f"`{amount}k`", inline=True)
            embed.add_field(
                name="Total Price",
                value=f"`{fmt_wl(total)}` {WL_EMOJI}",
                inline=True,
            )
            embed.add_field(name="Storage 1 World", value=storage, inline=False)
            embed.add_field(name="Storage 2 World", value=iddoor, inline=False)
            embed.add_field(name="Name Block / ID", value=item, inline=False)
        elif service_type == "splice_seed":
            embed.add_field(name="Jumlah World", value=f"`{amount}`", inline=True)
            embed.add_field(
                name="Total Price",
                value=f"`{fmt_wl(total)}` {WL_EMOJI}",
                inline=True,
            )
            embed.add_field(name="Storage IdDoor IdItem", value=storage, inline=False)
            embed.add_field(name="IdDoor World", value=iddoor, inline=False)
            embed.add_field(name="Nama World", value=worlds, inline=False)
        else:
            embed.add_field(name="Jumlah World", value=f"`{amount}`", inline=True)
            embed.add_field(
                name="Total Price",
                value=f"`{fmt_wl(total)}` {WL_EMOJI}",
                inline=True,
            )
            embed.add_field(name="Storage : IdDoor", value=storage, inline=False)
            embed.add_field(name="IdDoor Semua World", value=iddoor, inline=False)
            embed.add_field(name="Nama World", value=worlds, inline=False)
            embed.add_field(name="Seed/Block + ID", value=item, inline=False)

        if cancel_reason:
            embed.add_field(
                name="Alasan Cancel", value=cancel_reason, inline=False
            )
        
        price_unit = "1k Block" if service_type == "move_block_seed" else "World"
        embed.set_footer(
            text=f"Price {fmt_wl(price)} WL/{price_unit} • Order ID #{order_id}"
        )
        return embed

    def fetch_order(order_id):
        c.execute(
            """
            SELECT id, user_id, service_type, amount, storage, iddoor, worlds,
                   item, price, total, growid, status, cancel_reason
            FROM service_orders
            WHERE id=?
            """,
            (order_id,),
        )
        return c.fetchone()

    class ServiceOrderModal(Modal):
        def __init__(self, service_type):
            config = SERVICE_TYPES[service_type]
            super().__init__(
                title=f"Form {config['emoji']} {config['label']}",
                timeout=300,
            )
            self.service_type = service_type
            self.amount = TextInput(
                label="Masukan berapa jumlah world",
                placeholder="Contoh 1-100 (Hanya Angka)",
                required=True,
                max_length=3,
            )
            self.storage = TextInput(
                label="Nama Storage IdDoor",
                placeholder="Contoh: World:IdDoor",
                required=True,
                max_length=100,
            )
            self.iddoor = TextInput(
                label="IdDoor World wajib sama semua",
                placeholder="Contoh: promaxgile (bukan id storage)",
                required=True,
                max_length=100,
            )
            self.worlds = TextInput(
                label="Nama World Sesuai Jumlah Order",
                placeholder="Contoh: start,start50,zeus",
                required=True,
                style=discord.TextStyle.paragraph,
                max_length=1000,
            )
            self.item = TextInput(
                label="Nama Seed/block beserta idnya",
                placeholder="Contoh: Lgrid (29970)",
                required=True,
                max_length=200,
            )
            for field in (
                self.amount,
                self.storage,
                self.iddoor,
                self.worlds,
                self.item,
            ):
                self.add_item(field)

        async def on_submit(self, interaction):
            try:
                amount = int(self.amount.value.strip())
                if amount < 1 or amount > 100:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    "❌ Jumlah world wajib angka dari 1 sampai 100.",
                    ephemeral=True,
                )
                return

            values = {
                "storage": self.storage.value.strip(),
                "iddoor": self.iddoor.value.strip(),
                "worlds": self.worlds.value.strip(),
                "item": self.item.value.strip(),
            }
            if not all(values.values()):
                await interaction.response.send_message(
                    "❌ Semua kolom wajib diisi.", ephemeral=True
                )
                return

            world_names = parse_world_names(values["worlds"])
            if len(world_names) != amount:
                await interaction.response.send_message(
                    "❌ Format nama world tidak sesuai jumlah order.\n"
                    f"Jumlah world di atas: `{amount}`\n"
                    f"Nama world terisi: `{len(world_names)}`\n"
                    "Pisahkan dengan koma, contoh: `world1,world2,world3`.\n"
                    "Silakan buat ulang form dan isi sesuai jumlah world.",
                    ephemeral=True,
                )
                return
            values["worlds"] = ",".join(world_names)

            await interaction.response.defer(ephemeral=True)
            price = get_service_price(self.service_type)
            total = price * amount

            async with order_lock:
                c.execute(
                    "SELECT nama, balance FROM users WHERE user_id=?",
                    (interaction.user.id,),
                )
                user_row = c.fetchone()
                if not user_row:
                    await interaction.followup.send(
                        "❌ Kamu belum register. Klik **Set GrowID** di menu stock.",
                        ephemeral=True,
                    )
                    return

                growid, balance = user_row[0], int(user_row[1] or 0)
                if balance < total:
                    await interaction.followup.send(
                        f"❌ Saldo tidak cukup. Dibutuhkan `{fmt_wl(total)}` WL, "
                        f"saldo kamu `{fmt_wl(balance)}` WL.",
                        ephemeral=True,
                    )
                    return

                c.execute(
                    "UPDATE users SET balance=balance-? WHERE user_id=?",
                    (total, interaction.user.id),
                )
                c.execute(
                    """
                    INSERT INTO service_orders
                        (user_id, service_type, amount, storage, iddoor, worlds,
                         item, price, total, growid, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        interaction.user.id,
                        self.service_type,
                        amount,
                        values["storage"],
                        values["iddoor"],
                        values["worlds"],
                        values["item"],
                        price,
                        total,
                        growid,
                    ),
                )
                order_id = c.lastrowid
                conn.commit()

            order_channel = bot.get_channel(order_channel_id)
            if order_channel is None:
                try:
                    order_channel = await bot.fetch_channel(order_channel_id)
                except Exception:
                    order_channel = None

            if order_channel is None:
                async with order_lock:
                    c.execute(
                        "UPDATE users SET balance=balance+? WHERE user_id=?",
                        (total, interaction.user.id),
                    )
                    c.execute(
                        """
                        UPDATE service_orders
                        SET status='cancelled',
                            cancel_reason='Channel pencatatan order tidak ditemukan'
                        WHERE id=? AND status='pending'
                        """,
                        (order_id,),
                    )
                    conn.commit()
                await interaction.followup.send(
                    "❌ Channel order tidak ditemukan. Saldo sudah dikembalikan.",
                    ephemeral=True,
                )
                return

            try:
                row = fetch_order(order_id)
                order_message = await order_channel.send(
                    embed=build_order_embed(row),
                    view=OrderActionView(order_id, "pending"),
                )
                c.execute(
                    "UPDATE service_orders SET message_id=? WHERE id=?",
                    (order_message.id, order_id),
                )
                conn.commit()
            except Exception as exc:
                async with order_lock:
                    c.execute(
                        "UPDATE users SET balance=balance+? WHERE user_id=?",
                        (total, interaction.user.id),
                    )
                    c.execute(
                        """
                        UPDATE service_orders
                        SET status='cancelled',
                            cancel_reason='Gagal mengirim catatan order'
                        WHERE id=? AND status='pending'
                        """,
                        (order_id,),
                    )
                    conn.commit()
                print(f"[SERVICE] Gagal mencatat order #{order_id}: {exc}")
                await interaction.followup.send(
                    "❌ Gagal mencatat pesanan. Saldo sudah dikembalikan.",
                    ephemeral=True,
                )
                return

            new_balance = balance - total
            await interaction.followup.send(
                f"✅ **Order #{order_id} berhasil dicatat.**\n"
                f"Total: `{fmt_wl(total)}` {WL_EMOJI}\n"
                f"Sisa saldo: `{fmt_wl(new_balance)}` {WL_EMOJI}",
                ephemeral=True,
            )

    class MoveServiceOrderModal(Modal):
        def __init__(self, service_type):
            config = SERVICE_TYPES[service_type]
            super().__init__(
                title=f"Form {config['emoji']} {config['label']}",
                timeout=300,
            )
            self.service_type = service_type
            self.amount = TextInput(
                label="Masukin total block mu minimal 1-100k",
                placeholder="Contoh isi 1-100 jangan pakai k",
                required=True,
                max_length=3,
            )
            self.item = TextInput(
                label="Name Block / ID Block",
                placeholder="Contoh : Pepper / 1XXXX",
                required=True,
                max_length=100,
            )
            self.storage1 = TextInput(
                label="Storage 1 World",
                placeholder="World | IdDoor",
                required=True,
                max_length=100,
            )
            self.storage2 = TextInput(
                label="Storage 2 World",
                placeholder="World | IdDoor",
                required=True,
                max_length=100,
            )
            self.add_item(self.amount)
            self.add_item(self.item)
            self.add_item(self.storage1)
            self.add_item(self.storage2)

        async def on_submit(self, interaction):
            try:
                amount = int(self.amount.value.strip())
                if amount < 1:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    "❌ Jumlah block wajib angka minimal 1.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            price = get_service_price(self.service_type)
            total = price * amount

            async with order_lock:
                c.execute(
                    "SELECT nama, balance FROM users WHERE user_id=?",
                    (interaction.user.id,),
                )
                user_row = c.fetchone()
                if not user_row:
                    await interaction.followup.send(
                        "❌ Kamu belum register. Klik **Set GrowID** di menu stock.",
                        ephemeral=True,
                    )
                    return

                growid, balance = user_row[0], int(user_row[1] or 0)
                if balance < total:
                    await interaction.followup.send(
                        f"❌ Saldo tidak cukup. Dibutuhkan `{fmt_wl(total)}` WL, "
                        f"saldo kamu `{fmt_wl(balance)}` WL.",
                        ephemeral=True,
                    )
                    return

                c.execute(
                    "UPDATE users SET balance=balance-? WHERE user_id=?",
                    (total, interaction.user.id),
                )
                c.execute(
                    """
                    INSERT INTO service_orders
                        (user_id, service_type, amount, storage, iddoor, worlds,
                         item, price, total, growid, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        interaction.user.id,
                        self.service_type,
                        amount,
                        self.storage1.value.strip(),
                        self.storage2.value.strip(),
                        "N/A",
                        self.item.value.strip(),
                        price,
                        total,
                        growid,
                    ),
                )
                order_id = c.lastrowid
                conn.commit()

            order_channel = bot.get_channel(order_channel_id)
            if order_channel is None:
                try:
                    order_channel = await bot.fetch_channel(order_channel_id)
                except Exception:
                    order_channel = None

            if order_channel is None:
                async with order_lock:
                    c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (total, interaction.user.id))
                    c.execute("UPDATE service_orders SET status='cancelled', cancel_reason='Channel order tidak ditemukan' WHERE id=?", (order_id,))
                    conn.commit()
                await interaction.followup.send("❌ Channel order tidak ditemukan. Saldo dikembalikan.", ephemeral=True)
                return

            try:
                row = fetch_order(order_id)
                order_message = await order_channel.send(
                    embed=build_order_embed(row),
                    view=OrderActionView(order_id, "pending"),
                )
                c.execute("UPDATE service_orders SET message_id=? WHERE id=?", (order_message.id, order_id))
                conn.commit()
            except Exception as exc:
                async with order_lock:
                    c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (total, interaction.user.id))
                    c.execute("UPDATE service_orders SET status='cancelled', cancel_reason='Gagal kirim pesan' WHERE id=?", (order_id,))
                    conn.commit()
                await interaction.followup.send(f"❌ Gagal mencatat pesanan: {exc}", ephemeral=True)
                return

            new_balance = balance - total
            await interaction.followup.send(
                f"✅ **Order #{order_id} (Move Service) berhasil dicatat.**\n"
                f"Total: `{fmt_wl(total)}` {WL_EMOJI}\n"
                f"Sisa saldo: `{fmt_wl(new_balance)}` {WL_EMOJI}",
                ephemeral=True,
            )

    class SpliceServiceOrderModal(Modal):
        def __init__(self, service_type):
            config = SERVICE_TYPES[service_type]
            super().__init__(
                title=f"Form {config['emoji']} {config['label']}",
                timeout=300,
            )
            self.service_type = service_type
            self.amount = TextInput(
                label="Masukan berapa jumlah world",
                placeholder="Contoh 1-100 ( Hanya Angka )",
                required=True,
                max_length=3,
            )
            self.storage = TextInput(
                label="Nama Storage IdDoor IdItem",
                placeholder="Contoh : World:IdDoor:IdSeed/Block:IdSeed(splice)",
                required=True,
                max_length=200,
            )
            self.iddoor = TextInput(
                label="Tulis IdDoor World wajib sama semua",
                placeholder="Contoh : promaxgile ( bukan id storage )",
                required=True,
                max_length=100,
            )
            self.worlds = TextInput(
                label="Tulis Nama World Sesuai Jumlah Order",
                placeholder="contoh : start,start50,zeus",
                required=True,
                style=discord.TextStyle.paragraph,
                max_length=1000,
            )
            self.add_item(self.amount)
            self.add_item(self.storage)
            self.add_item(self.iddoor)
            self.add_item(self.worlds)

        async def on_submit(self, interaction):
            try:
                amount = int(self.amount.value.strip())
                if amount < 1:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    "❌ Jumlah world wajib angka minimal 1.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            price = get_service_price(self.service_type)
            total = price * amount

            async with order_lock:
                c.execute(
                    "SELECT nama, balance FROM users WHERE user_id=?",
                    (interaction.user.id,),
                )
                user_row = c.fetchone()
                if not user_row:
                    await interaction.followup.send(
                        "❌ Kamu belum register. Klik **Set GrowID** di menu stock.",
                        ephemeral=True,
                    )
                    return

                growid, balance = user_row[0], int(user_row[1] or 0)
                if balance < total:
                    await interaction.followup.send(
                        f"❌ Saldo tidak cukup. Dibutuhkan `{fmt_wl(total)}` WL, "
                        f"saldo kamu `{fmt_wl(balance)}` WL.",
                        ephemeral=True,
                    )
                    return

                c.execute(
                    "UPDATE users SET balance=balance-? WHERE user_id=?",
                    (total, interaction.user.id),
                )
                c.execute(
                    """
                    INSERT INTO service_orders
                        (user_id, service_type, amount, storage, iddoor, worlds,
                         item, price, total, growid, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        interaction.user.id,
                        self.service_type,
                        amount,
                        self.storage.value.strip(),
                        self.iddoor.value.strip(),
                        self.worlds.value.strip(),
                        "N/A",  # Item combined into storage field for splice
                        price,
                        total,
                        growid,
                    ),
                )
                order_id = c.lastrowid
                conn.commit()

            order_channel = bot.get_channel(order_channel_id)
            if order_channel is None:
                try:
                    order_channel = await bot.fetch_channel(order_channel_id)
                except Exception:
                    order_channel = None

            if order_channel is None:
                async with order_lock:
                    c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (total, interaction.user.id))
                    c.execute("UPDATE service_orders SET status='cancelled', cancel_reason='Channel order tidak ditemukan' WHERE id=?", (order_id,))
                    conn.commit()
                await interaction.followup.send("❌ Channel order tidak ditemukan. Saldo dikembalikan.", ephemeral=True)
                return

            try:
                row = fetch_order(order_id)
                order_message = await order_channel.send(
                    embed=build_order_embed(row),
                    view=OrderActionView(order_id, "pending"),
                )
                c.execute("UPDATE service_orders SET message_id=? WHERE id=?", (order_message.id, order_id))
                conn.commit()
            except Exception as exc:
                async with order_lock:
                    c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (total, interaction.user.id))
                    c.execute("UPDATE service_orders SET status='cancelled', cancel_reason='Gagal kirim pesan' WHERE id=?", (order_id,))
                    conn.commit()
                await interaction.followup.send(f"❌ Gagal mencatat pesanan: {exc}", ephemeral=True)
                return

            new_balance = balance - total
            await interaction.followup.send(
                f"✅ **Order #{order_id} (Splice Service) berhasil dicatat.**\n"
                f"Total: `{fmt_wl(total)}` {WL_EMOJI}\n"
                f"Sisa saldo: `{fmt_wl(new_balance)}` {WL_EMOJI}",
                ephemeral=True,
            )

    class CancelReasonModal(Modal, title="Alasan Cancel Service"):
        def __init__(self, order_id, source_message):
            super().__init__(timeout=180)
            self.order_id = order_id
            self.source_message = source_message
            self.reason = TextInput(
                label="Alasan pembatalan",
                placeholder="Contoh: World tidak dapat diakses",
                required=True,
                style=discord.TextStyle.paragraph,
                max_length=500,
            )
            self.add_item(self.reason)

        async def on_submit(self, interaction):
            await interaction.response.defer(ephemeral=True)
            reason = self.reason.value.strip()

            async with order_lock:
                row = fetch_order(self.order_id)
                if not row:
                    await interaction.followup.send(
                        "❌ Order tidak ditemukan.", ephemeral=True
                    )
                    return
                if row[11] in {"cancelled", "done"}:
                    await interaction.followup.send(
                        f"❌ Order sudah berstatus **{status_label(row[11])}**.",
                        ephemeral=True,
                    )
                    return

                user_id, total = row[1], int(row[9] or 0)
                c.execute(
                    """
                    UPDATE service_orders
                    SET status='cancelled', cancel_reason=?
                    WHERE id=? AND status IN ('pending', 'processing')
                    """,
                    (reason, self.order_id),
                )
                if c.rowcount != 1:
                    conn.rollback()
                    await interaction.followup.send(
                        "❌ Status order sudah berubah.", ephemeral=True
                    )
                    return
                c.execute(
                    "UPDATE users SET balance=balance+? WHERE user_id=?",
                    (total, user_id),
                )
                conn.commit()
                updated_row = fetch_order(self.order_id)

            await self.source_message.edit(
                embed=build_order_embed(updated_row),
                view=OrderActionView(self.order_id, "cancelled"),
            )
            try:
                user = await bot.fetch_user(updated_row[1])
                await user.send(
                    f"❌ **Service Order #{self.order_id} dibatalkan.**\n"
                    f"Alasan: {reason}\n"
                    f"Refund: `{fmt_wl(updated_row[9])}` WL."
                )
            except Exception:
                pass
            await interaction.followup.send(
                f"✅ Order dibatalkan dan `{fmt_wl(updated_row[9])}` WL "
                "sudah dikembalikan.",
                ephemeral=True,
            )

    class ServiceSelect(Select):
        def __init__(self):
            options = [
                discord.SelectOption(
                    label=config["label"],
                    value=service_type,
                    emoji=config["emoji"],
                    description=f"Price: {get_service_price(service_type)} WL/{'1k Block' if service_type == 'move_block_seed' else 'World'}",
                )
                for service_type, config in SERVICE_TYPES.items()
            ]
            super().__init__(
                placeholder="Pilih jasa yang ingin diorder...",
                min_values=1,
                max_values=1,
                options=options,
                custom_id="service:select",
            )

        async def callback(self, interaction):
            val = self.values[0]
            if val == "move_block_seed":
                await interaction.response.send_modal(MoveServiceOrderModal(val))
            elif val == "splice_seed":
                await interaction.response.send_modal(SpliceServiceOrderModal(val))
            else:
                await interaction.response.send_modal(ServiceOrderModal(val))

    class ServiceView(View):
        def __init__(self):
            super().__init__(timeout=None)
            # Service Button (Main)
            self.add_item(
                Button(
                    label="SERVICE",
                    emoji="🛠️",
                    style=discord.ButtonStyle.success,
                    custom_id="service_menu:main",
                )
            )
            # Stock Buttons
            self.add_item(
                Button(
                    label="Deposit WL",
                    style=discord.ButtonStyle.secondary,
                    custom_id="deposit",
                )
            )
            self.add_item(
                Button(
                    label="Depo QRIS",
                    style=discord.ButtonStyle.secondary,
                    custom_id="depo_qris",
                )
            )
            self.add_item(
                Button(
                    label="Set GrowID",
                    style=discord.ButtonStyle.gray,
                    custom_id="growid",
                )
            )
            self.add_item(
                Button(
                    label="My Balance",
                    style=discord.ButtonStyle.gray,
                    custom_id="balance",
                )
            )

    class ServiceSelectView(View):
        def __init__(self):
            super().__init__(timeout=60)
            self.add_item(ServiceSelect())

    class OrderActionView(View):
        def __init__(self, order_id, status):
            super().__init__(timeout=None)
            final = status in {"cancelled", "done"}
            self.add_item(
                Button(
                    label="Loading",
                    emoji="⏳",
                    style=discord.ButtonStyle.primary,
                    custom_id=f"service_order:loading:{order_id}",
                    disabled=final or status == "processing",
                )
            )
            self.add_item(
                Button(
                    label="Cancel",
                    emoji="✖️",
                    style=discord.ButtonStyle.danger,
                    custom_id=f"service_order:cancel:{order_id}",
                    disabled=final,
                )
            )
            self.add_item(
                Button(
                    label="Done",
                    emoji="✅",
                    style=discord.ButtonStyle.success,
                    custom_id=f"service_order:done:{order_id}",
                    disabled=final,
                )
            )

    async def resolve_channel(channel_id=None):
        channel_id = channel_id or get_saved_channel_id()
        if not channel_id:
            return None
        channel = bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await bot.fetch_channel(channel_id)
        except Exception as exc:
            print(f"[SERVICE] Gagal fetch channel {channel_id}: {exc}")
            return None

    async def reset_service_message(channel):
        message = await channel.send(embed=build_embed(), view=ServiceView())
        message_cache["channel_id"] = channel.id
        message_cache["message"] = message
        save_channel_id(channel.id)
        print(f"[SERVICE] Menu dipasang di channel {channel.id}")
        return message

    async def post_service_message(channel):
        # Cari pesan menu yang sudah ada di history (batas 10 pesan terakhir)
        async for msg in channel.history(limit=10):
            if msg.author.id == bot.user.id and msg.embeds and "SERVICE LIST" in (msg.embeds[0].title or ""):
                message_cache["channel_id"] = channel.id
                message_cache["message"] = msg
                print(f"[SERVICE] Menggunakan kembali pesan menu yang sudah ada di channel {channel.id}")
                return msg

        # Jika tidak ketemu, baru kirim baru
        message = await channel.send(embed=build_embed(), view=ServiceView())
        message_cache["channel_id"] = channel.id
        message_cache["message"] = message
        print(f"[SERVICE] Membuat pesan menu baru di channel {channel.id}")
        return message

    async def on_service_order_action(interaction):
        if interaction.type != discord.InteractionType.component:
            return
        data = getattr(interaction, "data", None) or {}
        custom_id = data.get("custom_id", "")

        # Handle Service Menu Buttons
        if custom_id.startswith("service_menu:"):
            _, action = custom_id.split(":", 1)
            if action == "main":
                await interaction.response.send_message(
                    "**🛠️ List Service:**\nSilakan pilih jasa dari menu di bawah ini:",
                    view=ServiceSelectView(),
                    ephemeral=True
                )
            else:
                if action == "move_block_seed":
                    await interaction.response.send_modal(MoveServiceOrderModal(action))
                elif action == "splice_seed":
                    await interaction.response.send_modal(SpliceServiceOrderModal(action))
                else:
                    await interaction.response.send_modal(ServiceOrderModal(action))
            return

        if not custom_id.startswith("service_order:"):
            return

        permissions = getattr(interaction.user, "guild_permissions", None)
        if not permissions or not permissions.administrator:
            await interaction.response.send_message(
                "❌ Tombol ini hanya untuk administrator.", ephemeral=True
            )
            return

        try:
            _, action, raw_order_id = custom_id.split(":", 2)
            order_id = int(raw_order_id)
        except (ValueError, TypeError):
            await interaction.response.send_message(
                "❌ ID order tidak valid.", ephemeral=True
            )
            return

        row = fetch_order(order_id)
        if not row:
            await interaction.response.send_message(
                "❌ Order tidak ditemukan.", ephemeral=True
            )
            return

        if action == "cancel":
            if row[11] in {"cancelled", "done"}:
                await interaction.response.send_message(
                    f"❌ Order sudah berstatus **{status_label(row[11])}**.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_modal(
                CancelReasonModal(order_id, interaction.message)
            )
            return

        if action == "loading":
            await interaction.response.defer(ephemeral=True)
            async with order_lock:
                row = fetch_order(order_id)
                if row[11] != "pending":
                    await interaction.followup.send(
                        f"❌ Order tidak bisa diproses dari status "
                        f"**{status_label(row[11])}**.",
                        ephemeral=True,
                    )
                    return

            try:
                service_type = row[2]
                label = SERVICE_TYPES[service_type]['label']
                if service_type == "move_block_seed":
                    detail = f"Total: `{row[3]}k` block\nStorage: `{row[4]}`"
                else:
                    detail = f"Jumlah: `{row[3]}` world"
                    if row[6] and row[6] != "N/A":
                        detail += f"\nNama world: `{row[6]}`"
                
                user = await bot.fetch_user(row[1])
                await user.send(
                    f"⏳ **Service Order #{order_id} sedang dikerjakan.**\n"
                    f"Service: {label}\n{detail}"
                )
            except Exception as exc:
                await interaction.followup.send(
                    f"❌ Gagal kirim notifikasi ke user. Status order belum diubah. Detail: `{exc}`",
                    ephemeral=True,
                )
                return

            async with order_lock:
                c.execute(
                    """
                    UPDATE service_orders SET status='processing'
                    WHERE id=? AND status='pending'
                    """,
                    (order_id,),
                )
                changed = c.rowcount
                conn.commit()
                row = fetch_order(order_id)
            if not changed:
                await interaction.followup.send(
                    f"❌ Status order berubah sebelum diproses. Sekarang: "
                    f"**{status_label(row[11])}**.",
                    ephemeral=True,
                )
                return

            await interaction.message.edit(
                embed=build_order_embed(row),
                view=OrderActionView(order_id, "processing"),
            )
            await interaction.followup.send(
                "✅ Order dipindah ke status loading dan user sudah dikirim notifikasi.",
                ephemeral=True,
            )
            return

        if action == "done":
            await interaction.response.defer(ephemeral=True)
            async with order_lock:
                row = fetch_order(order_id)
                if row[11] not in {"pending", "processing"}:
                    await interaction.followup.send(
                        f"❌ Order sudah berstatus **{status_label(row[11])}**.",
                        ephemeral=True,
                    )
                    return
                previous_status = row[11]
                c.execute(
                    """
                    UPDATE service_orders SET status='done'
                    WHERE id=? AND status IN ('pending', 'processing')
                    """,
                    (order_id,),
                )
                conn.commit()

                try:
                    service_type = row[2]
                    label = SERVICE_TYPES[service_type]['label']
                    if service_type == "move_block_seed":
                        detail = f"Total: `{row[3]}k` block"
                    else:
                        detail = f"Jumlah: `{row[3]}` world"
                    
                    user = await bot.fetch_user(row[1])
                    await user.send(
                        f"✅ **Service Order #{order_id} selesai!**\n"
                        f"Service: {label}\n"
                        f"{detail}\n"
                        "Terima kasih sudah menggunakan service kami."
                    )
                except Exception as exc:
                    c.execute(
                        """
                        UPDATE service_orders SET status=?
                        WHERE id=? AND status='done'
                        """,
                        (previous_status, order_id),
                    )
                    conn.commit()
                    await interaction.followup.send(
                        f"❌ Gagal DM user, status belum diubah. Detail: `{exc}`",
                        ephemeral=True,
                    )
                    return

                row = fetch_order(order_id)
            await interaction.message.edit(
                embed=build_order_embed(row),
                view=OrderActionView(order_id, "done"),
            )

            # ✅ Kirim Testimoni
            channel_testi_id = int(os.getenv("CHANNEL_TESTIMONI", "0"))
            channel = bot.get_channel(channel_testi_id)
            if channel:
                config = SERVICE_TYPES.get(row[2], {"label": "SERVICE", "emoji": "🛠️"})
                embed = discord.Embed(
                    title=f"#Order Number: {order_id}",
                    color=discord.Color.gold(),
                )
                embed.add_field(
                    name="<a:megaphone:1419515391851626580> Pembeli",
                    value=f"<@{row[1]}>",
                    inline=False,
                )
                embed.add_field(
                    name="Produk <a:menkrep:1122531571098980394>",
                    value=f"{config['emoji']} {config['label']}",
                    inline=False,
                )
                embed.add_field(
                    name="Total Price",
                    value=f"{fmt_wl(row[9])} <a:world_lock:1419515667773657109>",
                    inline=False,
                )
                embed.set_footer(text="Thanks For Using Our Jasa Service(s)")
                await channel.send(embed=embed)

            await interaction.followup.send(
                "✅ Order selesai dan user berhasil dikirim DM.", ephemeral=True
            )

    bot.add_listener(on_service_order_action, "on_interaction")

    @bot.listen("on_ready")
    async def auto_post_service_on_ready():
        nonlocal startup_initialized
        if startup_initialized:
            return
        startup_initialized = True
        channel = await resolve_channel()
        if channel is None:
            print("[SERVICE] Channel belum diatur. Jalankan !service di channel tujuan.")
            return
        try:
            # Check if we should start the loop
            if not update_service.is_running():
                update_service.start()
                print(f"[SERVICE] Loop started automatically for channel {channel.id}")
        except Exception as exc:
            print(f"[SERVICE] Gagal auto-start service: {exc}")

    guild_decorator = (
        app_commands.guilds(discord.Object(id=int(server_id_raw)))
        if server_id_raw
        else (lambda func: func)
    )

    @bot.hybrid_command(
        name="service",
        usage=f"{PREFIX}service",
        description="Tampilkan menu order Service",
    )
    @is_allowed_user()
    @is_maintenance()
    @guild_decorator
    async def service(ctx):
        save_channel_id(ctx.channel.id)
        await post_service_message(ctx.channel)
        if not update_service.is_running():
            update_service.start()

    @bot.hybrid_command(
        name="hargajasa",
        usage=f"{PREFIX}hargajasa <code> <price>",
        description="Update harga jasa service berdasarkan kode",
    )
    @is_allowed_user()
    @guild_decorator
    async def hargajasa(ctx, code: str, price: int):
        c.execute("SELECT code FROM service_prices WHERE code=?", (code,))
        if not c.fetchone():
            codes = ", ".join([cfg["code"] for cfg in SERVICE_TYPES.values()])
            await ctx.send(f"❌ Kode service tidak ditemukan. Pilih salah satu: `{codes}`", ephemeral=True)
            return
        
        c.execute("UPDATE service_prices SET price=? WHERE code=?", (price, code))
        conn.commit()
        await ctx.send(f"✅ Harga service dengan kode `{code}` berhasil diupdate menjadi `{fmt_wl(price)}` WL.", ephemeral=True)

    @tasks.loop(seconds=30)
    async def update_service():
        channel_id = message_cache["channel_id"] or get_saved_channel_id()
        if not channel_id:
            return
        channel = await resolve_channel(channel_id)
        if channel is None:
            return
        try:
            message = message_cache["message"]
            if message is None:
                await post_service_message(channel)
                return
            await message.edit(embed=build_embed(), view=ServiceView())
        except Exception:
            await post_service_message(channel)

    @update_service.before_loop
    async def before_update_service():
        await bot.wait_until_ready()
