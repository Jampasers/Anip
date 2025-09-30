import discord
from discord.ext import commands, tasks
import datetime
from ui_views import StockView
from utils import is_allowed_user, is_maintenance

def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register the stock command with auto-update functionality."""
    message_cache = {"channel_id": None, "message": None}

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
        embed.set_footer(text=f" Last Update: {datetime.datetime.now().strftime('%H:%M:%S')}")
        if not rows:
            embed.description = "❌ Belum ada stok barang."
            return embed
        desc_parts = []
        for (kode, judul, jumlah, harga) in rows:
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
                f"<a:toa:1122531485090582619>  {judul} ({kode.upper()})\n"
                f"<a:panah1:1419515217892606053>  Stock: {jumlah}\n"
                f"<a:panah1:1419515217892606053>  Price: {fmt_wl(harga)} <a:world_lock:1419515667773657109>\n"
                f"<a:panah1:1419515217892606053>  Product Sold: {sold}"
            )
            desc_parts.append(part)
        # Gabungkan antar produk dengan garis pemisah
        embed.description = "\n========================================\n".join(desc_parts)
        return embed

    # @bot.command(name="stock")
    @bot.hybrid_command(name="stcck",
                        usage=f"{PREFIX}stock",
                        description="Show stock")
    @is_allowed_user()  # hanya user di ALLOWED_USERNAMES
    @is_maintenance()
    async def stock(ctx):
        """Set channel untuk auto-update stock"""
        embed = build_embed()
        msg = await ctx.send(embed=embed, view=StockView())
        message_cache["channel_id"] = ctx.channel.id
        message_cache["message"] = msg
        if not update_stock.is_running():
            update_stock.start()

    @tasks.loop(seconds=10)
    async def update_stock():
        if message_cache["channel_id"] is None:
            return
        channel = bot.get_channel(message_cache["channel_id"])
        if channel is None:
            return
        embed = build_embed()
        try:
            await message_cache["message"].edit(embed=embed, view=StockView())
        except Exception:
            # kalau pesan hilang, kirim baru
            msg = await channel.send(embed=embed, view=StockView())
            message_cache["message"] = msg

    @update_stock.before_loop
    async def before_update_stock():
        await bot.wait_until_ready()
