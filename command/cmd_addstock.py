from discord.ext import commands
import re
import shlex
from utils import is_allowed_user, is_maintenance
import os
from dotenv import load_dotenv

load_dotenv()
CHANNEL_RESTOCK_NOTIF = int(os.getenv("CHANNEL_RESTOCK_NOTIF", "0"))

def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register the addstock command, which adds new products or items."""
    @bot.command(
        usage=f'{PREFIX}addstock <code> "<title>" <item1,item2,...>  OR  {PREFIX}addstock <code> <item1,item2,...>  OR  {PREFIX}addstock <code> <title> + attach .txt (1 item per line)'
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
        - Upload file (1 item per line, boleh ada koma di 1 baris):
          !addstock DF "Dirtfarm Bagus Banget" (attach file .txt)
          !addstock DF (attach file .txt)  -> tambah ke produk lama
        """
        # --- Mode upload file attachment (1 item per line; koma tidak di-split) ---
        if ctx.message.attachments:
            parts = args.split(maxsplit=1)
            if len(parts) < 1:
                await ctx.send("❌ Format salah. Gunakan: !addstock <code> <title> (attach file .txt)")
                return
            code = re.sub(r'[^a-z0-9]', '', parts[0].lower())
            title_arg = parts[1].strip() if len(parts) > 1 else ""

            attachment = ctx.message.attachments[0]
            try:
                raw = await attachment.read()
                content = raw.decode("utf-8", errors="ignore")
            except Exception:
                await ctx.send("❌ Gagal membaca file. Pastikan file .txt.")
                return

            items = [line.strip() for line in content.splitlines() if line.strip()]
            if not items:
                await ctx.send("❌ File kosong atau tidak ada item valid.")
                return

            if title_arg:
                # produk baru atau gunakan title dari db jika sudah ada
                c.execute("SELECT judul FROM stock WHERE kode = ?", (code,))
                row = c.fetchone()
                if row:
                    title = row[0]
                else:
                    title = title_arg
                    c.execute("INSERT INTO stock (kode, judul, harga) VALUES (?, ?, 0)", (code, title))
            else:
                # jika produk belum ada, auto buat judul = kode
                c.execute("SELECT judul FROM stock WHERE kode = ?", (code,))
                row = c.fetchone()
                if not row:
                    title = code
                    c.execute("INSERT INTO stock (kode, judul, harga) VALUES (?, ?, 0)", (code, title))
                else:
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
                    continue
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
            ch = bot.get_channel(CHANNEL_RESTOCK_NOTIF)
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

            # TRIGGER ALOKASI PO OTOMATIS
            if hasattr(bot, "allocate_preorders"):
                print(f"[ADDSTOCK] Triggering allocation for {code}...")
                await bot.allocate_preorders(code)
            return

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
        ch = bot.get_channel(CHANNEL_RESTOCK_NOTIF)
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

        # TRIGGER ALOKASI PO OTOMATIS
        if hasattr(bot, "allocate_preorders"):
            print(f"[ADDSTOCK] Triggering allocation for {code}...")
            await bot.allocate_preorders(code)

