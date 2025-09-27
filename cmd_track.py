from discord.ext import commands
from utils import is_allowed_user
import discord

def setup(bot, c, conn, fmt_wl, PREFIX):
    @bot.command(usage=f"{PREFIX}track <order_id>")
    @is_allowed_user()
    async def track(ctx, order_id: int):
        # === Cek transaksi BUY ===
        c.execute("""
            SELECT t.id, u.nama, t.user_id, t.kode, t.jumlah, s.harga, t.waktu
            FROM transactions t
            LEFT JOIN users u ON u.user_id = t.user_id
            LEFT JOIN stock s ON s.kode = t.kode
            WHERE t.id = ?
        """, (order_id,))
        trx = c.fetchone()

        if trx:
            trx_id, growid, uid, kode, jumlah, harga, created = trx
            total = (harga or 0) * jumlah

            # Ambil detail item
        c.execute("SELECT nama_barang FROM preorder_items WHERE preorder_id=?", (po_id,))
        items = [row[0] for row in c.fetchall()]

        if not items:
            if status == "waiting":
                item_list = "    (belum terpenuhi / masih waiting)    "
            else:
                item_list = "    (❌ ERROR: item tidak tercatat)    "
        else:
            item_list = "\n".join(items)

        embed.add_field(
            name="Items",
            value=f"```\n{item_list}\n```",
            inline=False
        )


        # === Cek Preorder (PO) ===
        c.execute("""
            SELECT p.id, u.nama, p.user_id, p.kode, p.amount, s.harga, p.status, p.created_at
            FROM preorders p
            LEFT JOIN users u ON u.user_id = p.user_id
            LEFT JOIN stock s ON s.kode = p.kode
            WHERE p.id = ?
        """, (order_id,))
        po = c.fetchone()

        if po:
            po_id, growid, uid, kode, jumlah, harga, status, created = po
            total = (harga or 0) * jumlah

            # Ambil detail item
            c.execute("SELECT nama_barang FROM preorder_items WHERE preorder_id=?", (po_id,))
            items = [row[0] for row in c.fetchall()]
            item_list = "\n".join(items) if items else "(belum terpenuhi / masih waiting)"

            embed = discord.Embed(
                title=f"📦 Order Detail (PO) #{po_id}",
                color=discord.Color.gold()
            )
            embed.add_field(name="Buyer", value=f"<@{uid}> ({growid})", inline=False)
            embed.add_field(name="Produk", value=f"{jumlah} x {kode}", inline=False)
            embed.add_field(name="Total Price", value=fmt_wl(total), inline=False)
            embed.add_field(name="Status", value=status, inline=False)
            embed.add_field(name="Items", value=f"```\n{item_list}\n```", inline=False)
            embed.add_field(name="Created At", value=str(created), inline=False)
            await ctx.send(embed=embed)
            return

        # === Kalau tidak ketemu ===
        await ctx.send(f"❌ Order dengan ID {order_id} tidak ditemukan.")
