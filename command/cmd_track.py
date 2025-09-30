from discord.ext import commands
from utils import is_allowed_user
import discord
from discord import app_commands
import os

def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register the track command to show transaction or preorder details."""
    # @bot.command(usage=f"{PREFIX}track <order_id>")
    @bot.hybrid_command(name="track",
                        usage=f"{PREFIX}track <order_id>",
                        description="Track order")
    @is_allowed_user()
    @app_commands.guilds(os.getenv("SERVER_ID"))
    async def track(ctx, order_id: int):
        # === Cek transaksi BUY ===
        c.execute(
            """
                SELECT t.id, u.nama, t.user_id, t.kode, t.jumlah, s.harga, t.waktu
                FROM transactions t
                LEFT JOIN users u ON u.user_id = t.user_id
                LEFT JOIN stock s ON s.kode = t.kode
                WHERE t.id = ?
            """,
            (order_id,),
        )
        trx = c.fetchone()
        if trx:
            trx_id, growid, uid, kode, jumlah, harga, created = trx
            total = (harga or 0) * jumlah
            # Ambil detail item
            c.execute("SELECT nama_barang FROM preorder_items WHERE preorder_id=?", (order_id,))
            items = [row[0] for row in c.fetchall()]
            item_list = "\n".join(items) if items else "    (belum terpenuhi / masih waiting)    "
            embed = discord.Embed(
                title=f" Order Detail #{order_id}",
                color=discord.Color.gold(),
            )
            embed.add_field(name="Buyer", value=f"<@{uid}> ({growid})", inline=False)
            embed.add_field(name="Produk", value=f"{jumlah} x {kode}", inline=False)
            embed.add_field(name="Total Price", value=fmt_wl(total), inline=False)
            embed.add_field(name="Items", value=f"```\n{item_list}\n```", inline=False)
            embed.add_field(name="Created At", value=str(created), inline=False)
            await ctx.send(embed=embed)
            return
        # === Kalau tidak ketemu ===
        await ctx.send(f"❌ Order dengan ID {order_id} tidak ditemukan.")
