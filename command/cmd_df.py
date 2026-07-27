import asyncio
import datetime
import io
import os
import re

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import tasks
from discord.ui import Button, Modal, TextInput, View

from utils import is_allowed_user, is_maintenance


WL_EMOJI = "<a:world_lock:1419515667773657109>"
DEFAULT_DF_PRICE = 45
PRODUCT_NAME = "Dirt Farm 5 Letter NN VIA PUT LOCK"


def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register Dirt Farm menu, stock, pricing, and unlock workflow."""
    message_cache = {"channel_id": None, "message": None}
    startup_initialized = False
    callback_server_started = False
    callback_runner = None
    df_lock = asyncio.Lock()
    unlock_ephemeral_messages = {}

    server_id_raw = os.getenv("SERVER_ID", "").strip()
    env_channel_id = int(os.getenv("DF_CHANNEL_ID", "0") or 0)
    testimonial_channel_id = int(
        os.getenv("DF_TESTIMONI_CHANNEL_ID", os.getenv("CHANNEL_TESTIMONI", "0")) or 0
    )
    callback_host = os.getenv("DF_UNLOCK_CALLBACK_HOST", "127.0.0.1").strip()
    callback_port = int(os.getenv("DF_UNLOCK_CALLBACK_PORT", "8765") or 8765)

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
        CREATE TABLE IF NOT EXISTS df_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    c.execute(
        "INSERT OR IGNORE INTO df_settings (key, value) VALUES ('price', ?)",
        (str(DEFAULT_DF_PRICE),),
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS df_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            growid TEXT,
            amount INTEGER NOT NULL,
            price INTEGER NOT NULL,
            total INTEGER NOT NULL,
            status TEXT DEFAULT 'completed',
            message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS df_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            world_name TEXT NOT NULL,
            status TEXT DEFAULT 'available',
            buyer_id INTEGER,
            order_id INTEGER,
            unlock_request_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS df_unlock_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            growid TEXT,
            qty INTEGER NOT NULL,
            worlds TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            message_id INTEGER,
            dm_message_id INTEGER,
            logs TEXT DEFAULT '',
            bot_name TEXT,
            bot_status TEXT,
            bot_world TEXT,
            timer_total INTEGER,
            timer_remaining INTEGER,
            buyer_status TEXT,
            testimonial_message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
        """
    )
    columns = {
        row[1] for row in c.execute("PRAGMA table_info(df_unlock_requests)").fetchall()
    }
    migrations = {
        "dm_message_id": "ALTER TABLE df_unlock_requests ADD COLUMN dm_message_id INTEGER",
        "logs": "ALTER TABLE df_unlock_requests ADD COLUMN logs TEXT DEFAULT ''",
        "bot_name": "ALTER TABLE df_unlock_requests ADD COLUMN bot_name TEXT",
        "bot_status": "ALTER TABLE df_unlock_requests ADD COLUMN bot_status TEXT",
        "bot_world": "ALTER TABLE df_unlock_requests ADD COLUMN bot_world TEXT",
        "timer_total": "ALTER TABLE df_unlock_requests ADD COLUMN timer_total INTEGER",
        "timer_remaining": "ALTER TABLE df_unlock_requests ADD COLUMN timer_remaining INTEGER",
        "buyer_status": "ALTER TABLE df_unlock_requests ADD COLUMN buyer_status TEXT",
        "testimonial_message_id": "ALTER TABLE df_unlock_requests ADD COLUMN testimonial_message_id INTEGER",
    }
    for column, query in migrations.items():
        if column not in columns:
            c.execute(query)
    conn.commit()

    def get_saved_channel_id():
        if env_channel_id:
            return env_channel_id
        c.execute("SELECT value FROM bot_settings WHERE key='df_channel_id'")
        row = c.fetchone()
        try:
            return int(row[0]) if row else 0
        except (TypeError, ValueError):
            return 0

    def save_channel_id(channel_id):
        c.execute(
            """
            INSERT INTO bot_settings (key, value)
            VALUES ('df_channel_id', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str(channel_id),),
        )
        conn.commit()

    def get_saved_message_id():
        c.execute("SELECT value FROM bot_settings WHERE key='df_message_id'")
        row = c.fetchone()
        try:
            return int(row[0]) if row else 0
        except (TypeError, ValueError):
            return 0

    def save_message_id(message_id):
        c.execute(
            """
            INSERT INTO bot_settings (key, value)
            VALUES ('df_message_id', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str(message_id),),
        )
        conn.commit()

    def get_df_price():
        c.execute("SELECT value FROM df_settings WHERE key='price'")
        row = c.fetchone()
        try:
            return max(0, int(row[0])) if row else DEFAULT_DF_PRICE
        except (TypeError, ValueError):
            return DEFAULT_DF_PRICE

    def set_df_price(price):
        c.execute(
            """
            INSERT INTO df_settings (key, value)
            VALUES ('price', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str(price),),
        )
        conn.commit()

    def count_available_stock():
        c.execute("SELECT COUNT(*) FROM df_stock WHERE status='available'")
        return int(c.fetchone()[0] or 0)

    def parse_worlds(raw_value):
        parts = re.split(r"[\s,]+", raw_value.strip())
        return [part.strip().upper() for part in parts if part.strip()]

    def build_menu_embed():
        price = get_df_price()
        stock = count_available_stock()
        embed = discord.Embed(
            title="DIRT FARM",
            description=(
                f"**{PRODUCT_NAME}**\n\n"
                f"<a:panah1:1419515217892606053> **Price:** `{fmt_wl(price)}` {WL_EMOJI} / World\n"
                f"<a:panah1:1419515217892606053> **Stock:** `{stock}` World"
            ),
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Menu",
            value="Gunakan tombol di bawah untuk beli, cek world, atau request unlock.",
            inline=False,
        )
        embed.set_footer(
            text=f"Last Update: {datetime.datetime.now().strftime('%H:%M:%S')}"
        )
        return embed

    def status_label(status):
        return {
            "pending": "PENDING BREAK LOCK",
            "processing": "BOT PROCESSING",
            "done": "BREAK LOCK DONE",
            "cancelled": "CANCELLED",
            "failed": "FAILED",
        }.get(status, status.upper())

    def fetch_unlock_request(request_id):
        c.execute(
            """
            SELECT
                id, user_id, growid, qty, worlds, status, COALESCE(logs, ''),
                dm_message_id, COALESCE(bot_name, ''), COALESCE(bot_status, ''),
                COALESCE(bot_world, ''), timer_total, timer_remaining,
                COALESCE(buyer_status, '')
            FROM df_unlock_requests
            WHERE id=?
            """,
            (request_id,),
        )
        return c.fetchone()

    def mask_world_name(world_name):
        world_name = str(world_name or "").strip().upper()
        if not world_name:
            return "-"
        return f"{world_name[0]}{'x' * max(3, len(world_name) - 1)}"

    def build_testimonial_embed(order_id):
        c.execute(
            """
            SELECT id, user_id, amount, total
            FROM df_orders
            WHERE id=?
            """,
            (order_id,),
        )
        row = c.fetchone()
        if not row:
            return None

        _, user_id, amount, total = row
        c.execute(
            "SELECT world_name FROM df_stock WHERE order_id=? ORDER BY id",
            (order_id,),
        )
        worlds = [mask_world_name(item[0]) for item in c.fetchall()]
        embed = discord.Embed(
            title=f"#Order Number: {order_id}",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="<a:megaphone:1419515391851626580> Pembeli",
            value=f"<@{user_id}>",
            inline=False,
        )
        embed.add_field(
            name="Produk <a:menkrep:1122531571098980394>",
            value=f"{amount}x {PRODUCT_NAME}",
            inline=False,
        )
        embed.add_field(
            name="World",
            value="\n".join(worlds[:30]) or "-",
            inline=False,
        )
        if len(worlds) > 30:
            embed.add_field(name="Sisa", value=f"+{len(worlds) - 30} world lain", inline=False)
        embed.add_field(
            name="Total Price",
            value=f"{fmt_wl(total)} {WL_EMOJI}",
            inline=False,
        )
        embed.set_footer(text="Thanks For Purchasing Our Product(s)")
        return embed

    def build_unlock_testimonial_embed(request_id):
        c.execute(
            """
            SELECT id, user_id, growid, qty, worlds
            FROM df_unlock_requests
            WHERE id=? AND status='done'
            """,
            (request_id,),
        )
        row = c.fetchone()
        if not row:
            return None

        _, user_id, growid, qty, worlds = row
        world_list = [
            mask_world_name(world)
            for world in str(worlds or "").splitlines()
            if world.strip()
        ]
        embed = discord.Embed(
            title=f"#Unlock Number: {request_id}",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="<a:megaphone:1419515391851626580> Pembeli",
            value=f"<@{user_id}> (`{growid or '-'}`)",
            inline=False,
        )
        embed.add_field(
            name="Produk <a:menkrep:1122531571098980394>",
            value=f"{qty}x Break Lock {PRODUCT_NAME}",
            inline=False,
        )
        embed.add_field(
            name="World",
            value="\n".join(world_list[:30]) or "-",
            inline=False,
        )
        if len(world_list) > 30:
            embed.add_field(name="Sisa", value=f"+{len(world_list) - 30} world lain", inline=False)
        embed.add_field(name="Status", value="BREAK LOCK DONE", inline=False)
        embed.set_footer(text="Thanks For Using Our Unlock Service")
        return embed

    def build_unlock_world_text(request_id, worlds, request_status):
        world_list = [world.strip() for world in str(worlds or "").split("\n") if world.strip()]
        status_map = {}
        if request_id:
            c.execute(
                """
                SELECT UPPER(world_name), status
                FROM df_stock
                WHERE unlock_request_id=?
                """,
                (request_id,),
            )
            status_map = {row[0]: row[1] for row in c.fetchall()}

        lines = []
        for world_name in world_list:
            world_key = world_name.upper()
            world_status = status_map.get(world_key, "")
            if request_status == "done" or world_status == "unlocked":
                icon = "\u2705"
            elif request_status in {"failed", "cancelled"}:
                icon = "\u274c"
            else:
                icon = "\u23f3"
            lines.append(f"{icon} {world_name}")

        return "\n".join(lines) or "-"

    def format_unlock_timer(status, timer_total, timer_remaining):
        if status == "done":
            return "SELESAI"
        if status in {"failed", "cancelled"}:
            return "HABIS" if timer_remaining == 0 else "-"

        try:
            remaining = int(timer_remaining)
        except (TypeError, ValueError):
            remaining = None
        try:
            total = int(timer_total)
        except (TypeError, ValueError):
            total = None

        if remaining is None:
            if total is None:
                return "MENUNGGU BOT"
            minutes = max(1, total // 60)
            return f"{minutes} MENIT"

        remaining = max(0, remaining)
        if total is not None:
            remaining = min(remaining, max(0, total))
        return f"{remaining // 60:02d}:{remaining % 60:02d}"

    def build_unlock_embed(row):
        request_id, user_id, growid, qty, worlds, status = row[:6]
        bot_name = row[8] if len(row) > 8 and row[8] else "-"
        bot_status = row[9] if len(row) > 9 and row[9] else "-"
        bot_world = row[10] if len(row) > 10 and row[10] else "-"
        timer_total = row[11] if len(row) > 11 else None
        timer_remaining = row[12] if len(row) > 12 else None
        buyer_status = row[13] if len(row) > 13 and row[13] else "-"
        color = {
            "pending": discord.Color.gold(),
            "processing": discord.Color.blue(),
            "done": discord.Color.green(),
            "cancelled": discord.Color.red(),
            "failed": discord.Color.red(),
        }.get(status, discord.Color.light_grey())
        world_list = [world for world in worlds.split("\n") if world.strip()]
        world_text = build_unlock_world_text(request_id, worlds, status)
        embed = discord.Embed(
            title=f"DF Unlock Request #{request_id}",
            description=f"Status: **{status_label(status)}**",
            color=color,
        )
        embed.add_field(
            name="Bot Unlock",
            value=f"Nama: `{bot_name}`\nStatus: `{bot_status}`\nWorld: `{bot_world}`",
            inline=False,
        )
        embed.add_field(name="BUYER", value=buyer_status, inline=False)
        embed.add_field(name="Buyer", value=f"<@{user_id}> (`{growid or '-'}`)", inline=False)
        embed.add_field(name="Jumlah Unlock", value=f"`{qty}` World", inline=True)
        embed.add_field(name="World", value=world_text, inline=False)
        embed.add_field(
            name="TIMER",
            value=format_unlock_timer(status, timer_total, timer_remaining),
            inline=False,
        )
        if len(world_list) > 30:
            embed.add_field(name="Sisa", value=f"+{len(world_list) - 30} world lain", inline=False)
        return embed

    def make_world_text(rows):
        if not rows:
            return "Belum ada world DF aktif."
        lines = []
        for index, (world_name, status, order_id, unlock_request_id) in enumerate(rows, start=1):
            suffix = ""
            if status == "unlocking":
                suffix = f" - WAIT BREAK #{unlock_request_id}"
            lines.append(f"{index}. {world_name}{suffix} (Order #{order_id})")
        return "\n".join(lines)

    async def send_long_text(interaction, title, text):
        if len(text) <= 1800:
            await interaction.response.send_message(f"**{title}**\n```{text}```", ephemeral=True)
            return
        payload = io.BytesIO(text.encode("utf-8"))
        await interaction.response.send_message(
            f"**{title}**",
            file=discord.File(payload, filename="df_my_world.txt"),
            ephemeral=True,
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
            print(f"[DF] Gagal fetch channel {channel_id}: {exc}")
            return None

    async def resolve_testimonial_channel():
        if not testimonial_channel_id:
            return None
        channel = bot.get_channel(testimonial_channel_id)
        if channel is not None:
            return channel
        try:
            return await bot.fetch_channel(testimonial_channel_id)
        except Exception as exc:
            print(
                f"[DF] Gagal fetch testimonial channel {testimonial_channel_id}: {exc}"
            )
            return None

    async def send_unlock_testimonial_once(request_id):
        async with df_lock:
            c.execute(
                """
                SELECT testimonial_message_id
                FROM df_unlock_requests
                WHERE id=? AND status='done'
                """,
                (request_id,),
            )
            row = c.fetchone()
            if not row or row[0] is not None:
                return

            c.execute(
                """
                UPDATE df_unlock_requests
                SET testimonial_message_id=-1
                WHERE id=? AND status='done' AND testimonial_message_id IS NULL
                """,
                (request_id,),
            )
            if c.rowcount == 0:
                conn.commit()
                return
            conn.commit()

        testimonial_channel = await resolve_testimonial_channel()
        testimonial_embed = build_unlock_testimonial_embed(request_id)
        if testimonial_channel is None or testimonial_embed is None:
            async with df_lock:
                c.execute(
                    """
                    UPDATE df_unlock_requests
                    SET testimonial_message_id=NULL
                    WHERE id=? AND testimonial_message_id=-1
                    """,
                    (request_id,),
                )
                conn.commit()
            return

        try:
            msg = await testimonial_channel.send(embed=testimonial_embed)
            async with df_lock:
                c.execute(
                    """
                    UPDATE df_unlock_requests
                    SET testimonial_message_id=?
                    WHERE id=?
                    """,
                    (msg.id, request_id),
                )
                conn.commit()
        except Exception as exc:
            print(f"[DF] Gagal kirim testimoni unlock #{request_id}: {exc}")
            async with df_lock:
                c.execute(
                    """
                    UPDATE df_unlock_requests
                    SET testimonial_message_id=NULL
                    WHERE id=? AND testimonial_message_id=-1
                    """,
                    (request_id,),
                )
                conn.commit()

    async def resolve_unlock_channel(default_channel=None):
        channel = await resolve_channel()
        if channel is not None:
            return channel
        return default_channel

    async def append_unlock_log(request_id, message, status=None):
        text = str(message or "").strip()
        if not text:
            return fetch_unlock_request(request_id)
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {text}"
        c.execute(
            "SELECT COALESCE(logs, ''), status FROM df_unlock_requests WHERE id=?",
            (request_id,),
        )
        row = c.fetchone()
        previous = row[0] if row else ""
        current_status = row[1] if row else None
        lines = (previous.splitlines() + [line])[-40:]
        if status:
            terminal_statuses = {"done", "failed", "cancelled"}
            next_status = str(status)
            if current_status in terminal_statuses and next_status not in terminal_statuses:
                next_status = current_status
            c.execute(
                "UPDATE df_unlock_requests SET logs=?, status=? WHERE id=?",
                ("\n".join(lines), next_status, request_id),
            )
        else:
            c.execute(
                "UPDATE df_unlock_requests SET logs=? WHERE id=?",
                ("\n".join(lines), request_id),
            )
        conn.commit()
        return fetch_unlock_request(request_id)

    async def edit_unlock_messages(request_id):
        row = fetch_unlock_request(request_id)
        if not row:
            return

        ephemeral_message = unlock_ephemeral_messages.get(request_id)
        if ephemeral_message is not None:
            try:
                await ephemeral_message.edit(embed=build_unlock_embed(row), view=None)
            except Exception as exc:
                print(f"[DF] Gagal edit ephemeral unlock #{request_id}: {exc}")

        c.execute("SELECT message_id FROM df_unlock_requests WHERE id=?", (request_id,))
        message_row = c.fetchone()
        message_id = int(message_row[0] or 0) if message_row else 0
        if message_id:
            channel = await resolve_unlock_channel()
            if channel is not None:
                try:
                    msg = await channel.fetch_message(message_id)
                    await msg.edit(embed=build_unlock_embed(row), view=None)
                except Exception as exc:
                    print(f"[DF] Gagal edit unlock log #{request_id}: {exc}")

    async def refresh_unlock_messages_on_startup():
        c.execute(
            """
            SELECT id
            FROM df_unlock_requests
            WHERE message_id IS NOT NULL
            ORDER BY id DESC
            LIMIT 25
            """
        )
        request_ids = [row[0] for row in c.fetchall()]
        for request_id in request_ids:
            await edit_unlock_messages(request_id)

    async def mark_world_unlocked(request_id, world_name):
        c.execute(
            """
            UPDATE df_stock
            SET status='unlocked'
            WHERE unlock_request_id=?
              AND UPPER(world_name)=UPPER(?)
              AND status='unlocking'
            """,
            (request_id, world_name),
        )
        conn.commit()

    async def finish_request_if_all_worlds_done(request_id):
        c.execute(
            "SELECT COUNT(*) FROM df_stock WHERE unlock_request_id=? AND status='unlocking'",
            (request_id,),
        )
        remaining = int(c.fetchone()[0] or 0)
        if remaining == 0:
            c.execute(
                """
                UPDATE df_unlock_requests
                SET status='done', completed_at=CURRENT_TIMESTAMP
                WHERE id=? AND status IN ('pending', 'processing')
                """,
                (request_id,),
            )
            conn.commit()
            await append_unlock_log(request_id, "Semua world sudah break lock. Request DONE.")
        return remaining == 0

    async def handle_unlock_next(request):
        request_id = 0
        try:
            async with df_lock:
                c.execute(
                    "SELECT id FROM df_unlock_requests WHERE status='processing' ORDER BY id LIMIT 1"
                )
                if c.fetchone():
                    return web.Response(
                        text="ok=false\nmessage=busy",
                        content_type="text/plain",
                    )

                c.execute(
                    """
                    SELECT id, growid, worlds
                    FROM df_unlock_requests
                    WHERE status='pending'
                    ORDER BY id
                    LIMIT 1
                    """
                )
                row = c.fetchone()
                if not row:
                    return web.Response(
                        text="ok=false\nmessage=no_job",
                        content_type="text/plain",
                    )

                request_id, growid, worlds = row
                c.execute(
                    """
                    UPDATE df_unlock_requests
                    SET status='processing'
                    WHERE id=? AND status='pending'
                    """,
                    (request_id,),
                )
                conn.commit()

            await append_unlock_log(
                request_id,
                "Lucifer polling mengambil request. Bot unlock mulai proses.",
                status="processing",
            )
            await edit_unlock_messages(request_id)

            compact_worlds = ",".join(
                world.strip() for world in str(worlds or "").splitlines() if world.strip()
            )
            return web.Response(
                text=(
                    "ok=true\n"
                    f"request_id={request_id}\n"
                    f"growid={growid or ''}\n"
                    f"worlds={compact_worlds}"
                ),
                content_type="text/plain",
            )
        except Exception as exc:
            print(f"[DF] Unlock next error: {exc}")
            if request_id:
                await append_unlock_log(request_id, f"Gagal kasih job ke Lucifer: {exc}", status="failed")
                await edit_unlock_messages(request_id)
            return web.Response(text=f"ok=false\nmessage={exc}", status=500)

    async def handle_unlock_log(request):
        try:
            data = await request.post()
            if not data:
                try:
                    data = await request.json()
                except Exception:
                    data = {}
            request_id = int(data.get("request_id", 0) or 0)
            message = str(data.get("message", "") or "")
            event = str(data.get("event", "") or "")
            world = str(data.get("world", "") or "")
            status = data.get("status")
            bot_name = str(data.get("bot_name", "") or "").strip()
            bot_status = str(data.get("bot_status", "") or "").strip()
            bot_world = str(data.get("bot_world", "") or "").strip()
            buyer_status = str(data.get("buyer_status", "") or "").strip()
            timer_total_raw = str(data.get("timer_total", "") or "").strip()
            timer_remaining_raw = str(data.get("timer_remaining", "") or "").strip()
            timer_total = int(timer_total_raw) if timer_total_raw.isdigit() else None
            timer_remaining = int(timer_remaining_raw) if timer_remaining_raw.isdigit() else None
            if timer_total is not None:
                timer_total = max(0, timer_total)
            if timer_remaining is not None:
                timer_remaining = max(0, timer_remaining)
                if timer_total is not None:
                    timer_remaining = min(timer_remaining, timer_total)

            if request_id <= 0:
                return web.Response(text="ok=false\nmessage=invalid request_id", status=400)

            should_send_unlock_testimonial = False
            async with df_lock:
                if bot_name or bot_status or bot_world:
                    c.execute(
                        """
                        UPDATE df_unlock_requests
                        SET bot_name=COALESCE(NULLIF(?, ''), bot_name),
                            bot_status=COALESCE(NULLIF(?, ''), bot_status),
                            bot_world=COALESCE(NULLIF(?, ''), bot_world)
                        WHERE id=?
                        """,
                        (bot_name, bot_status, bot_world, request_id),
                    )
                    conn.commit()
                if timer_total is not None or timer_remaining is not None:
                    c.execute(
                        """
                        UPDATE df_unlock_requests
                        SET timer_total=COALESCE(?, timer_total),
                            timer_remaining=COALESCE(?, timer_remaining)
                        WHERE id=?
                        """,
                        (timer_total, timer_remaining, request_id),
                    )
                    conn.commit()
                if buyer_status:
                    c.execute(
                        """
                        UPDATE df_unlock_requests
                        SET buyer_status=?
                        WHERE id=?
                        """,
                        (buyer_status, request_id),
                    )
                    conn.commit()
                if event == "world_done" and world:
                    await mark_world_unlocked(request_id, world)
                row = None
                if event != "timer":
                    row = await append_unlock_log(request_id, message, status=status)
                if event == "request_done":
                    c.execute(
                        """
                        UPDATE df_unlock_requests
                        SET status='done', completed_at=CURRENT_TIMESTAMP, timer_remaining=0
                        WHERE id=? AND status IN ('pending', 'processing')
                        """,
                        (request_id,),
                    )
                    conn.commit()
                    should_send_unlock_testimonial = True
                elif event == "world_done":
                    should_send_unlock_testimonial = await finish_request_if_all_worlds_done(request_id)
                elif event == "request_failed":
                    c.execute(
                        "UPDATE df_unlock_requests SET status='failed', timer_remaining=0 WHERE id=? AND status IN ('pending', 'processing')",
                        (request_id,),
                    )
                    c.execute(
                        """
                        UPDATE df_stock
                        SET status='owned', unlock_request_id=NULL
                        WHERE unlock_request_id=? AND status='unlocking'
                        """,
                        (request_id,),
                    )
                    conn.commit()

            await edit_unlock_messages(request_id)
            if should_send_unlock_testimonial:
                await send_unlock_testimonial_once(request_id)
            return web.Response(text="ok=true")
        except Exception as exc:
            print(f"[DF] Unlock callback error: {exc}")
            return web.Response(text=f"ok=false\nmessage={exc}", status=500)

    async def start_unlock_callback_server():
        nonlocal callback_server_started, callback_runner
        if callback_server_started:
            return
        app = web.Application()
        app.router.add_post("/df/unlock/log", handle_unlock_log)
        app.router.add_get("/df/unlock/next", handle_unlock_next)
        app.router.add_post("/df/unlock/next", handle_unlock_next)
        app.router.add_get("/df/unlock/health", lambda request: web.Response(text="ok=true"))
        callback_runner = web.AppRunner(app)
        await callback_runner.setup()
        site = web.TCPSite(callback_runner, callback_host, callback_port)
        await site.start()
        callback_server_started = True
        print(f"[DF] Unlock callback server listening on {callback_host}:{callback_port}")

    def reset_incomplete_unlocks_on_startup():
        c.execute(
            """
            UPDATE df_stock
            SET status='owned', unlock_request_id=NULL
            WHERE status='unlocking'
            """
        )
        restored_worlds = c.rowcount
        c.execute(
            """
            DELETE FROM df_unlock_requests
            WHERE status IN ('pending', 'processing', 'failed')
            """
        )
        deleted_requests = c.rowcount
        conn.commit()
        if restored_worlds or deleted_requests:
            print(
                f"[DF] Reset incomplete unlocks on startup: "
                f"worlds_restored={restored_worlds}, requests_deleted={deleted_requests}"
            )

    async def post_df_message(channel):
        saved_message_id = get_saved_message_id()
        if saved_message_id:
            try:
                msg = await channel.fetch_message(saved_message_id)
                if msg.author.id == bot.user.id and msg.embeds and "DIRT FARM" in (msg.embeds[0].title or ""):
                    message_cache["channel_id"] = channel.id
                    message_cache["message"] = msg
                    return msg
            except Exception:
                pass

        async for msg in channel.history(limit=200):
            if msg.author.id == bot.user.id and msg.embeds and "DIRT FARM" in (msg.embeds[0].title or ""):
                message_cache["channel_id"] = channel.id
                message_cache["message"] = msg
                save_message_id(msg.id)
                return msg
        message = await channel.send(embed=build_menu_embed(), view=DFView())
        message_cache["channel_id"] = channel.id
        message_cache["message"] = message
        save_message_id(message.id)
        return message

    class BuyDFModal(Modal):
        def __init__(self):
            super().__init__(title="Buy Dirt Farm", timeout=300)
            self.amount = TextInput(
                label="Jumlah Dirt Farm",
                placeholder="Contoh: 2",
                required=True,
                max_length=4,
            )
            self.add_item(self.amount)

        async def on_submit(self, interaction):
            try:
                amount = int(self.amount.value.strip())
                if amount < 1 or amount > 1000:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    "Jumlah wajib angka minimal 1 dan maksimal 1000.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            async with df_lock:
                price = get_df_price()
                total = price * amount
                c.execute("SELECT nama, balance FROM users WHERE user_id=?", (interaction.user.id,))
                user_row = c.fetchone()
                if not user_row:
                    await interaction.followup.send(
                        "Kamu belum register. Klik Set GrowID dulu.",
                        ephemeral=True,
                    )
                    return

                growid, balance = user_row[0], int(user_row[1] or 0)
                if balance < total:
                    await interaction.followup.send(
                        f"Saldo tidak cukup. Butuh `{fmt_wl(total)}` WL, saldo kamu `{fmt_wl(balance)}` WL.",
                        ephemeral=True,
                    )
                    return

                c.execute(
                    """
                    SELECT id, world_name
                    FROM df_stock
                    WHERE status='available'
                    ORDER BY id
                    LIMIT ?
                    """,
                    (amount,),
                )
                stock_rows = c.fetchall()
                if len(stock_rows) < amount:
                    await interaction.followup.send(
                        f"Stock tidak cukup. Tersedia `{len(stock_rows)}` world.",
                        ephemeral=True,
                    )
                    return

                c.execute(
                    """
                    INSERT INTO df_orders (user_id, growid, amount, price, total, status)
                    VALUES (?, ?, ?, ?, ?, 'completed')
                    """,
                    (interaction.user.id, growid, amount, price, total),
                )
                order_id = c.lastrowid
                ids = [row[0] for row in stock_rows]
                placeholders = ",".join(["?"] * len(ids))
                c.execute(
                    f"""
                    UPDATE df_stock
                    SET status='owned', buyer_id=?, order_id=?
                    WHERE id IN ({placeholders})
                    """,
                    (interaction.user.id, order_id, *ids),
                )
                c.execute(
                    "UPDATE users SET balance=balance-? WHERE user_id=?",
                    (total, interaction.user.id),
                )
                conn.commit()

            testimonial_channel = await resolve_testimonial_channel()
            if testimonial_channel is not None:
                try:
                    testimonial_embed = build_testimonial_embed(order_id)
                    if testimonial_embed is not None:
                        msg = await testimonial_channel.send(embed=testimonial_embed)
                        c.execute(
                            "UPDATE df_orders SET message_id=? WHERE id=?",
                            (msg.id, order_id),
                        )
                        conn.commit()
                except Exception as exc:
                    print(f"[DF] Gagal kirim testimoni order #{order_id}: {exc}")

            worlds = "\n".join([row[1] for row in stock_rows])
            await interaction.followup.send(
                f"Order DF #{order_id} berhasil.\n"
                f"Jumlah: `{amount}` World\n"
                f"Total: `{fmt_wl(total)}` {WL_EMOJI}\n"
                f"Sisa saldo: `{fmt_wl(balance - total)}` {WL_EMOJI}\n\n"
                f"World kamu:\n```{worlds}```",
                ephemeral=True,
            )

    class UnlockDFModal(Modal):
        def __init__(self):
            super().__init__(title="Unlock Dirt Farm", timeout=300)
            self.amount = TextInput(
                label="Jumlah world yang mau di-unlock",
                placeholder="Contoh: 2",
                required=True,
                max_length=4,
            )
            self.add_item(self.amount)

        async def on_submit(self, interaction):
            try:
                qty = int(self.amount.value.strip())
                if qty < 1 or qty > 1000:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message(
                    "Jumlah unlock wajib angka minimal 1 dan maksimal 1000.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)

            async with df_lock:
                c.execute("SELECT nama FROM users WHERE user_id=?", (interaction.user.id,))
                user_row = c.fetchone()
                growid = user_row[0] if user_row else None

                c.execute(
                    """
                    SELECT id
                    FROM df_unlock_requests
                    WHERE status IN ('pending', 'processing')
                    ORDER BY id
                    LIMIT 1
                    """
                )
                active_unlock = c.fetchone()
                if active_unlock:
                    await interaction.followup.send(
                        f"Masih ada proses unlock berjalan `#{active_unlock[0]}`. Tunggu selesai/gagal dulu.",
                        ephemeral=True,
                    )
                    return

                c.execute(
                    """
                    SELECT id, world_name
                    FROM df_stock
                    WHERE buyer_id=? AND status='owned'
                    ORDER BY id
                    LIMIT ?
                    """,
                    (interaction.user.id, qty),
                )
                rows = c.fetchall()
                if len(rows) < qty:
                    c.execute(
                        "SELECT COUNT(*) FROM df_stock WHERE buyer_id=? AND status='owned'",
                        (interaction.user.id,),
                    )
                    available = int(c.fetchone()[0] or 0)
                    c.execute(
                        "SELECT COUNT(*) FROM df_stock WHERE buyer_id=? AND status='unlocking'",
                        (interaction.user.id,),
                    )
                    waiting = int(c.fetchone()[0] or 0)
                    await interaction.followup.send(
                        f"Tidak bisa unlock `{qty}` world. Tersedia untuk unlock `{available}`, "
                        f"menunggu break lock `{waiting}`.",
                        ephemeral=True,
                    )
                    return

                worlds = "\n".join([row[1] for row in rows])
                c.execute(
                    """
                    INSERT INTO df_unlock_requests (user_id, growid, qty, worlds, status)
                    VALUES (?, ?, ?, ?, 'pending')
                    """,
                    (interaction.user.id, growid, qty, worlds),
                )
                request_id = c.lastrowid
                ids = [row[0] for row in rows]
                placeholders = ",".join(["?"] * len(ids))
                c.execute(
                    f"""
                    UPDATE df_stock
                    SET status='unlocking', unlock_request_id=?
                    WHERE id IN ({placeholders})
                    """,
                    (request_id, *ids),
                )
                conn.commit()

            try:
                request_row = fetch_unlock_request(request_id)
                msg = await interaction.followup.send(
                    embed=build_unlock_embed(request_row),
                    ephemeral=True,
                    wait=True,
                )
                unlock_ephemeral_messages[request_id] = msg
                c.execute(
                    "UPDATE df_unlock_requests SET message_id=?, dm_message_id=? WHERE id=?",
                    (None, None, request_id),
                )
                conn.commit()
            except Exception as exc:
                async with df_lock:
                    c.execute(
                        """
                        UPDATE df_stock
                        SET status='owned', unlock_request_id=NULL
                        WHERE unlock_request_id=? AND status='unlocking'
                        """,
                        (request_id,),
                    )
                    c.execute(
                        "UPDATE df_unlock_requests SET status='cancelled' WHERE id=?",
                        (request_id,),
                    )
                    conn.commit()
                await interaction.followup.send(
                    f"Gagal membuat request unlock. World tidak dihapus dari My World. Detail: `{exc}`",
                    ephemeral=True,
                )
                return

            await append_unlock_log(
                request_id,
                f"Unlock button triggered by {growid or interaction.user.id}. Menunggu polling Lucifer.",
                status="pending",
            )
            await edit_unlock_messages(request_id)
            request_row = fetch_unlock_request(request_id)

    class DFView(View):
        def __init__(self):
            super().__init__(timeout=None)
            self.add_item(
                Button(
                    label="Buy DF",
                    style=discord.ButtonStyle.success,
                    custom_id="df_menu:buy",
                )
            )
            self.add_item(
                Button(
                    label="My World",
                    style=discord.ButtonStyle.primary,
                    custom_id="df_menu:my_world",
                )
            )
            self.add_item(
                Button(
                    label="Unlock",
                    style=discord.ButtonStyle.secondary,
                    custom_id="df_menu:unlock",
                )
            )
            self.add_item(Button(label="Deposit WL", style=discord.ButtonStyle.secondary, custom_id="deposit"))
            self.add_item(Button(label="Depo QRIS", style=discord.ButtonStyle.secondary, custom_id="depo_qris"))
            self.add_item(Button(label="Set GrowID", style=discord.ButtonStyle.gray, custom_id="growid"))
            self.add_item(Button(label="My Balance", style=discord.ButtonStyle.gray, custom_id="balance"))

    async def on_df_interaction(interaction):
        if interaction.type != discord.InteractionType.component:
            return
        data = getattr(interaction, "data", None) or {}
        custom_id = data.get("custom_id", "")

        if custom_id == "df_menu:buy":
            await interaction.response.send_modal(BuyDFModal())
            return

        if custom_id == "df_menu:unlock":
            await interaction.response.send_modal(UnlockDFModal())
            return

        if custom_id == "df_menu:my_world":
            c.execute(
                """
                SELECT world_name, status, order_id, unlock_request_id
                FROM df_stock
                WHERE buyer_id=? AND status IN ('owned', 'unlocking')
                ORDER BY id
                """,
                (interaction.user.id,),
            )
            rows = c.fetchall()
            await send_long_text(interaction, "My DF World", make_world_text(rows))
            return

    bot.add_listener(on_df_interaction, "on_interaction")

    @bot.listen("on_ready")
    async def auto_post_df_on_ready():
        nonlocal startup_initialized
        try:
            await start_unlock_callback_server()
        except Exception as exc:
            print(f"[DF] Gagal start unlock callback server: {exc}")
        if startup_initialized:
            return
        startup_initialized = True
        reset_incomplete_unlocks_on_startup()
        await refresh_unlock_messages_on_startup()
        channel = await resolve_channel()
        if channel is None:
            print("[DF] Channel belum diatur. Jalankan !df di channel tujuan.")
            return
        if not update_df.is_running():
            update_df.start()

    guild_decorator = (
        app_commands.guilds(discord.Object(id=int(server_id_raw)))
        if server_id_raw
        else (lambda func: func)
    )

    @bot.hybrid_command(
        name="df",
        usage=f"{PREFIX}df",
        description="Tampilkan menu Dirt Farm",
    )
    @is_allowed_user()
    @is_maintenance()
    @guild_decorator
    async def df(ctx):
        save_channel_id(ctx.channel.id)
        await post_df_message(ctx.channel)
        if not update_df.is_running():
            update_df.start()

    @bot.hybrid_command(
        name="hargadf",
        usage=f"{PREFIX}hargadf <price>",
        description="Update harga Dirt Farm",
    )
    @is_allowed_user()
    @guild_decorator
    async def hargadf(ctx, price: int):
        if price < 0:
            await ctx.send("Harga tidak boleh minus.", ephemeral=True)
            return
        set_df_price(price)
        await ctx.send(
            f"Harga DF berhasil diupdate menjadi `{fmt_wl(price)}` WL.",
            ephemeral=True,
        )

    @bot.command(name="adddfstock", usage=f"{PREFIX}adddfstock <world1,world2,...>")
    @is_allowed_user()
    @is_maintenance()
    async def adddfstock(ctx, *, worlds: str):
        names = parse_worlds(worlds)
        if not names:
            await ctx.send("Isi minimal 1 world. Contoh: `!adddfstock ABCDE,FGHIJ`")
            return

        added = 0
        for name in names:
            c.execute(
                "INSERT INTO df_stock (world_name, status) VALUES (?, 'available')",
                (name,),
            )
            added += 1
        conn.commit()
        await ctx.send(
            f"Berhasil tambah `{added}` stock DF. Stock tersedia sekarang `{count_available_stock()}`."
        )

    @bot.command(name="deletedf", usage=f"{PREFIX}deletedf <world>")
    @is_allowed_user()
    @is_maintenance()
    async def deletedf(ctx, *, world: str):
        names = parse_worlds(world)
        if len(names) != 1:
            await ctx.send("Isi tepat 1 nama world. Contoh: `!deletedf SADAS`")
            return

        world_name = names[0]
        async with df_lock:
            c.execute(
                """
                SELECT id, world_name, status, buyer_id, order_id, unlock_request_id
                FROM df_stock
                WHERE UPPER(world_name)=UPPER(?)
                ORDER BY id
                """,
                (world_name,),
            )
            rows = c.fetchall()
            if not rows:
                await ctx.send(f"World `{world_name}` tidak ditemukan di stock DF / My World.")
                return

            affected_unlock_ids = sorted(
                {
                    int(row[5])
                    for row in rows
                    if row[5] is not None and str(row[2] or "").lower() == "unlocking"
                }
            )
            c.execute(
                "DELETE FROM df_stock WHERE UPPER(world_name)=UPPER(?)",
                (world_name,),
            )
            deleted_count = c.rowcount

            unlock_updates = []
            for request_id in affected_unlock_ids:
                c.execute(
                    "SELECT worlds, status FROM df_unlock_requests WHERE id=?",
                    (request_id,),
                )
                request_row = c.fetchone()
                if not request_row:
                    continue

                current_worlds, request_status = request_row
                remaining_worlds = [
                    item.strip()
                    for item in str(current_worlds or "").splitlines()
                    if item.strip() and item.strip().upper() != world_name
                ]
                if remaining_worlds:
                    c.execute(
                        """
                        UPDATE df_unlock_requests
                        SET worlds=?, qty=?
                        WHERE id=?
                        """,
                        ("\n".join(remaining_worlds), len(remaining_worlds), request_id),
                    )
                    unlock_updates.append(f"Unlock request #{request_id} diupdate")
                else:
                    next_status = (
                        "cancelled"
                        if request_status in {"pending", "processing"}
                        else request_status
                    )
                    c.execute(
                        """
                        UPDATE df_unlock_requests
                        SET worlds='', qty=0, status=?, completed_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (next_status, request_id),
                    )
                    unlock_updates.append(f"Unlock request #{request_id} dikosongkan")

            conn.commit()

        status_counts = {}
        buyer_ids = set()
        order_ids = set()
        for _, _, status, buyer_id, order_id, _ in rows:
            status_counts[status] = status_counts.get(status, 0) + 1
            if buyer_id:
                buyer_ids.add(int(buyer_id))
            if order_id:
                order_ids.add(int(order_id))

        status_text = ", ".join(
            f"{status or '-'}: {count}" for status, count in sorted(status_counts.items())
        )
        buyer_text = ", ".join(f"<@{buyer_id}>" for buyer_id in sorted(buyer_ids)) or "-"
        order_text = ", ".join(f"#{order_id}" for order_id in sorted(order_ids)) or "-"
        extra_text = "\n".join(unlock_updates) if unlock_updates else "-"
        await ctx.send(
            f"Berhasil hapus `{deleted_count}` data world `{world_name}` dari DF.\n"
            f"Status terhapus: `{status_text}`\n"
            f"Buyer terkait: {buyer_text}\n"
            f"Order terkait: `{order_text}`\n"
            f"Unlock update: `{extra_text}`"
        )

    @bot.hybrid_command(
        name="dfstock",
        usage=f"{PREFIX}dfstock",
        description="Cek stock Dirt Farm",
    )
    @is_allowed_user()
    @guild_decorator
    async def dfstock(ctx):
        c.execute("SELECT COUNT(*) FROM df_stock WHERE status='available'")
        available = int(c.fetchone()[0] or 0)
        c.execute("SELECT COUNT(*) FROM df_stock WHERE status='owned'")
        owned = int(c.fetchone()[0] or 0)
        c.execute("SELECT COUNT(*) FROM df_stock WHERE status='unlocking'")
        unlocking = int(c.fetchone()[0] or 0)
        c.execute("SELECT COUNT(*) FROM df_stock WHERE status='unlocked'")
        unlocked = int(c.fetchone()[0] or 0)
        await ctx.send(
            "```DF Stock\n"
            f"Available : {available}\n"
            f"Owned     : {owned}\n"
            f"Unlocking : {unlocking}\n"
            f"Unlocked  : {unlocked}```",
            ephemeral=True,
        )

    @tasks.loop(seconds=30)
    async def update_df():
        channel_id = message_cache["channel_id"] or get_saved_channel_id()
        if not channel_id:
            return
        channel = await resolve_channel(channel_id)
        if channel is None:
            return
        try:
            message = message_cache["message"]
            if message is None:
                await post_df_message(channel)
                return
            await message.edit(embed=build_menu_embed(), view=DFView())
        except Exception:
            await post_df_message(channel)

    @update_df.before_loop
    async def before_update_df():
        await bot.wait_until_ready()
