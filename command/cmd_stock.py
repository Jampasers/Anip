import datetime
import os

import discord
from discord import app_commands
from discord.ext import tasks

from ui_views import StockView
from utils import is_allowed_user, is_maintenance


def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register the stock command and keep one auto-refreshed stock message alive."""
    message_cache = {"channel_id": None, "message": None}
    startup_stock_initialized = False
    stock_channel_id = int(os.getenv("STOCK_CHANNEL_ID", "1415989274544836610"))
    server_id_raw = os.getenv("SERVER_ID", "").strip()

    def build_embed():
        c.execute(
            """
                SELECT s.kode, s.judul, COUNT(i.id) as jumlah, s.harga
                FROM stock s
                LEFT JOIN stock_items i ON s.kode = i.kode
                GROUP BY s.kode, s.judul, s.harga
                ORDER BY s.judul ASC
            """
        )
        rows = c.fetchall()
        embed = discord.Embed(
            title="<a:exclamation:1419518587072282654> PRODUCT LIST <a:exclamation:1419518587072282654>",
            color=discord.Color.red(),
        )
        embed.set_footer(
            text=f" Last Update: {datetime.datetime.now().strftime('%H:%M:%S')}"
        )
        if not rows:
            embed.description = "Belum ada stok barang."
            return embed

        desc_parts = []
        for kode, judul, jumlah, harga in rows:
            try:
                c.execute(
                    "SELECT COALESCE(SUM(jumlah), 0) FROM transactions WHERE LOWER(kode)=LOWER(?)",
                    (kode,),
                )
                sold = c.fetchone()[0]
            except Exception:
                c.execute(
                    "SELECT COUNT(*) FROM transactions WHERE LOWER(kode)=LOWER(?)",
                    (kode,),
                )
                sold = c.fetchone()[0]

            part = (
                f"<a:toa:1122531485090582619>  **{judul}** (`{kode.upper()}`)\n"
                f"<a:panah1:1419515217892606053>  **Stock:** `{jumlah}`\n"
                f"<a:panah1:1419515217892606053>  **Price:** `{fmt_wl(harga)}` <a:world_lock:1419515667773657109>\n"
                f"<a:panah1:1419515217892606053>  **Product Sold:** `{sold}`"
            )
            desc_parts.append(part)

        separator = "\n" + ("=" * 28) + "\n"
        embed.description = separator.join(desc_parts)
        return embed

    async def resolve_stock_channel():
        channel = bot.get_channel(stock_channel_id)
        if channel is not None:
            return channel
        try:
            return await bot.fetch_channel(stock_channel_id)
        except Exception as exc:
            print(f"[STOCK] Gagal fetch channel {stock_channel_id}: {exc}")
            return None

    async def reset_stock_message(channel):
        deleted = 0
        async for msg in channel.history(limit=None):
            try:
                await msg.delete()
                deleted += 1
            except discord.Forbidden:
                raise
            except discord.HTTPException:
                pass

        msg = await channel.send(embed=build_embed(), view=StockView())
        message_cache["channel_id"] = channel.id
        message_cache["message"] = msg
        print(
            f"[STOCK] Reset stock message in channel {channel.id} (deleted {deleted} messages)"
        )
        return msg

    async def post_or_refresh_stock(channel):
        if channel is None:
            return None
        msg = await channel.send(embed=build_embed(), view=StockView())
        message_cache["channel_id"] = channel.id
        message_cache["message"] = msg
        return msg

    @bot.listen("on_ready")
    async def auto_post_stock_on_ready():
        nonlocal startup_stock_initialized
        if startup_stock_initialized:
            return
        startup_stock_initialized = True

        channel = await resolve_stock_channel()
        if channel is None:
            return

        try:
            await reset_stock_message(channel)
            if not update_stock.is_running():
                update_stock.start()
        except discord.Forbidden:
            print(f"[STOCK] Tidak punya izin clear/send di channel {stock_channel_id}")
        except Exception as exc:
            print(f"[STOCK] Gagal reset stock message: {exc}")

    guild_decorator = (
        app_commands.guilds(discord.Object(id=int(server_id_raw)))
        if server_id_raw
        else (lambda func: func)
    )

    @bot.hybrid_command(
        name="stock",
        usage=f"{PREFIX}stock",
        description="Show stock",
    )
    @is_allowed_user()
    @is_maintenance()
    @guild_decorator
    async def stock(ctx):
        """Reset channel ini dan kirim satu pesan stock baru."""
        await reset_stock_message(ctx.channel)
        if not update_stock.is_running():
            update_stock.start()

    @tasks.loop(seconds=10)
    async def update_stock():
        if message_cache["channel_id"] is None:
            return

        channel = bot.get_channel(message_cache["channel_id"])
        if channel is None:
            channel = await resolve_stock_channel()
        if channel is None:
            return

        try:
            await message_cache["message"].edit(embed=build_embed(), view=StockView())
        except Exception:
            await post_or_refresh_stock(channel)

    @update_stock.before_loop
    async def before_update_stock():
        await bot.wait_until_ready()
