from discord.ext import commands
import re
import shlex
from utils import is_allowed_user, is_maintenance

def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register the addstock command, which adds new products or items."""
    @bot.command(
        usage=f'{PREFIX}addstock <code> "<title>" <item1,item2,...>  OR  {PREFIX}addstock <code> <item1,item2,...>'
    )
    @is_allowed_user()  # hanya user di ALLOWED_USERNAMES
    @is_maintenance()
    async def addstock(ctx, *, args: str):
        """
        Dua mode:
        - Produk baru:
          !addstock DF "Dirtfarm Bagus Banget" DF1,DF2,DF3
        - Tambah item ke produk lama:
          !addstock DF DF4,DF5,DF6
        """
        # --- Mode produk baru (pakai kutip di args) ---
        if '"' in args:
            try:
                parts = shlex.split(args)
            except Exception:
                await ctx.send(
                    "❌ Format salah. Gunakan kutip ganda untuk title. Contoh:\n"
                    f'{PREFIX}addstock DF "Dirtfarm Bagus Banget" DF1,DF2,DF3'
                )
                return
            if len(parts) < 3:
                await ctx.send("❌ Format salah. Gunakan: !addstock <code> \"<title>\" <item1,item2,...>")
                return
            code = re.sub(r'[^a-z0-9]', '', parts[0].lower())
            title = parts[1].strip()
            items_raw = " ".join(parts[2:])
            items = [x.strip() for x in items_raw.split(",") if x.strip()]
            if not title or not items:
                await ctx.send("❌ Harus ada title dan minimal 1 item.")
                return
            # Cek apakah kode sudah ada
            c.execute("SELECT judul FROM stock WHERE kode = ?", (code,))
            if c.fetchone():
                await ctx.send(f"❌ Kode `{code}` sudah ada. Gunakan format tanpa kutip untuk menambah item.")
                return
            # Insert produk baru
            c.execute("INSERT INTO stock (kode, judul, harga) VALUES (?, ?, 0)", (code, title))
        # --- Mode tambah item ke produk lama (tanpa kutip) ---
        else:
            parts = args.split(maxsplit=1)
            if len(parts) < 2:
                await ctx.send("❌ Format salah. Gunakan: !addstock <code> <item1,item2,...>")
                return
            code = re.sub(r'[^a-z0-9]', '', parts[0].lower())
            items_raw = parts[1]
            items = [x.strip() for x in items_raw.split(",") if x.strip()]
            if not items:
                await ctx.send("❌ Minimal 1 item harus disediakan.")
                return
            # Pastikan kode sudah ada
            c.execute("SELECT judul FROM stock WHERE kode = ?", (code,))
            row = c.fetchone()
            if not row:
                await ctx.send(f"❌ Produk dengan kode `{code}` belum ada. Gunakan format dengan kutip untuk membuat baru.")
                return
            title = row[0]
        # Insert item unik (hindari duplikat)
        seen = set()
        added = 0
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            c.execute("SELECT 1 FROM stock_items WHERE kode = ? AND nama_barang = ?", (code, item))
            if c.fetchone():
                continue  # skip duplikat
            c.execute("INSERT INTO stock_items (kode, nama_barang) VALUES (?, ?)", (code, item))
            added += 1
        conn.commit()
        total = c.execute("SELECT COUNT(*) FROM stock_items WHERE kode=?", (code,)).fetchone()[0]
        await ctx.send(
            "``` Stock Updated\n"
            "--------------------------\n"
            f"Code   : {code}\n"
            f"Title  : {title}\n"
            f"Added  : {added}\n"
            f"Total  : {total}```"
        )
        ch = bot.get_channel(1419609322886791168)
        if ch:
            await ch.send(
                "@everyone\n"
                "``` Stock Updated\n"
                "--------------------------\n"
                f"Code   : {code}\n"
                f"Title  : {title}\n"
                f"Added  : {added}\n"
                f"Total  : {total}```"
            )
