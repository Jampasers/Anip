"""
Discord Bot (Multi-file) with Full UI
Run this file: python bot_core.py
"""
import discord
from discord.ext import commands, tasks
import sqlite3
import re
from dotenv import load_dotenv
import os
import importlib
import pkgutil

load_dotenv()

# ===== Config =====
PREFIX = os.getenv("PREFIX")
BOT_TOKEN = os.getenv("BOT_TOKEN")

print(PREFIX)
print(BOT_TOKEN)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# ===== Database =====
DB_NAME = "discord_sqlite_bot.db"
conn = sqlite3.connect(DB_NAME, check_same_thread=False)
c = conn.cursor()

# Schema (idempotent)
c.execute(
    """CREATE TABLE IF NOT EXISTS users (
    nama TEXT PRIMARY KEY,
    balance INTEGER DEFAULT 0
)"""
)

try:
    c.execute("ALTER TABLE users ADD COLUMN poin INTEGER DEFAULT 0")
except sqlite3.OperationalError:
    pass  # kalau udah ada, abaikan

try:
    c.execute("ALTER TABLE users ADD COLUMN user_id INTEGER")
except sqlite3.OperationalError:
    pass

c.execute(
    """CREATE TABLE IF NOT EXISTS maintenance (
    is_mt INTEGER DEFAULT 0
)"""
)
c.execute("INSERT OR IGNORE INTO maintenance (rowid, is_mt) VALUES (1, 0)")
c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)")
conn.commit()
c.execute(
    """CREATE TABLE IF NOT EXISTS stock (
    kode TEXT PRIMARY KEY,
    judul TEXT,
    harga INTEGER DEFAULT 0
)"""
)
c.execute(
    """CREATE TABLE IF NOT EXISTS stock_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kode TEXT,
    nama_barang TEXT
)"""
)
c.execute(
    """CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    kode TEXT,
    jumlah INTEGER,
    waktu TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)"""
)
try:
    c.execute("ALTER TABLE transactions ADD COLUMN harga INTEGER")
except sqlite3.OperationalError:
    pass
c.execute("UPDATE transactions SET harga = 10 WHERE harga IS NULL")
conn.commit()

def fmt_wl(x: int) -> str:
    """Format integer values with thousands separators using dots."""
    try:
        return f"{int(x):,}".replace(",", ".")
    except Exception:
        return str(x)

# ===== Load modules =====
# Dynamically import all Python modules inside the `command` directory and call their setup functions.
commands_dir = os.path.join(os.path.dirname(__file__), "command")
for _, module_name, _ in pkgutil.iter_modules([commands_dir]):
    mod = importlib.import_module(f"command.{module_name}")
    if hasattr(mod, "setup"):
        try:
            mod.setup(bot, c, conn, fmt_wl, PREFIX)
        except TypeError:
            # Some modules (e.g. cmd_status) require DB_NAME as extra argument
            mod.setup(bot, c, conn, fmt_wl, PREFIX, DB_NAME)

# Import UI views and initialize last so it can hook listeners
import ui_views

TARGET_CHANNEL_ID = 1415979811154821170  # ganti dengan ID channel webhook game



# ============================================================
# Engine: Alokasi PO saat restock
# ============================================================
async def allocate_preorders(kode: str):
    """
    Jalankan fungsi ini SETIAP kali stok untuk `kode` ditambahkan (restock).
    - Ambil daftar PO waiting (FIFO).
    - Ambil item dari stock_items sesuai jatah user.
    - Kirim DM struk. Kalau DM gagal -> cancel PO & lanjut.
    - Partial fulfill: kalau stok habis di tengah, sisa amount tetap waiting.
    """
    # Hitung stok dulu
    c.execute("SELECT COUNT(*) FROM stock_items WHERE kode=?", (kode,))
    stock_available = int(c.fetchone()[0] or 0)
    if stock_available <= 0:
        return

    # (Opsional) ambil harga untuk info
    c.execute("SELECT harga FROM stock WHERE kode=?", (kode,))
    pr = c.fetchone()
    price = int(pr[0]) if pr else 0

    # Ambil queue PO
    c.execute(
        """
        SELECT id, user_id, nama, amount 
        FROM preorders
        WHERE kode=? AND status='waiting'
        ORDER BY created_at ASC, id ASC
    """,
        (kode,),
    )
    queue = c.fetchall()

    for po_id, user_id, growid, amount in queue:
        if stock_available <= 0:
            break

        # Jatah user = min(amount, stock_available)
        jatah = min(amount, stock_available)
        if jatah <= 0:
            continue

        # Ambil item
        c.execute(
            "SELECT id, nama_barang FROM stock_items WHERE kode=? ORDER BY id LIMIT ?",
            (kode, jatah),
        )
        items = c.fetchall()
        if not items or len(items) < jatah:
            # stok berubah, refresh count & lanjut
            c.execute("SELECT COUNT(*) FROM stock_items WHERE kode=?", (kode,))
            stock_available = int(c.fetchone()[0] or 0)
            continue

        ids = [str(x[0]) for x in items]
        bought_names = "\n".join([x[1] for x in items])

        # Coba DM sebelum commit
        member = bot.get_user(user_id)
        try:
            dm_msg = (
                "```🛒 Pre Order Success!\n"
                "--------------------------\n"
                f"Code   : {kode}\n"
                f"Amount : {jatah}\n"
                f"Price  : {price}\n"
                f"Total  : {price*jatah}\n\n"
                f"📦 Items:\n{bought_names}```"
            )
            if member is None:
                raise RuntimeError("Member not found in cache")
            await member.send(dm_msg)
        except Exception:
            # DM gagal -> cancel PO & lanjut user berikutnya
            c.execute("UPDATE preorders SET status='cancelled' WHERE id=?", (po_id,))
            conn.commit()
            continue

        # DM sukses -> commit transaksi
        c.execute(
            f"DELETE FROM stock_items WHERE id IN ({','.join(['?']*len(ids))})", ids
        )
        c.execute(
            "INSERT INTO transactions (user_id, kode, jumlah) VALUES (?, ?, ?)",
            (user_id, kode, jatah),
        )
        transaction_id = c.lastrowid

        c.executemany(
    "INSERT INTO preorder_items (preorder_id, nama_barang) VALUES (?, ?)",
    [(po_id, x[1]) for x in items]
)

        
        stock_available -= jatah

        if jatah == amount:
            # terpenuhi semua
            c.execute("UPDATE preorders SET status='success' WHERE id=?", (po_id,))
            channel_od = 839981637567643668
            channel = bot.get_channel(channel_od)
            if channel:
                embed = discord.Embed(
                    title=f"#Pesanan Pre Order Number: {transaction_id}",
                    color=discord.Color.gold()
                )
                embed.add_field(name="<a:megaphone:1419515391851626580> Pembeli", value=f"<@{user_id}> ({growid})", inline=False)
                embed.add_field(name="Produk <a:menkrep:1122531571098980394>", value=f"{jatah} {kode}", inline=False)
                embed.add_field(name="Total Price", value=f"{fmt_wl(price * jatah)} <a:world_lock:1419515667773657109>", inline=False)
                embed.set_footer(text="Thanks For Purchasing Our Product(s)")
                await channel.send(embed=embed)

        else:
            # partial fulfill -> sisa tetap waiting (kurangi amount)
            sisa = amount - jatah
            c.execute("UPDATE preorders SET amount=? WHERE id=?", (sisa, po_id))

        conn.commit()

@tasks.loop(seconds=10)  # jalan tiap 10 detik
async def auto_allocate_po():
    # Ambil semua kode produk yg ada preorder waiting
    c.execute("SELECT DISTINCT kode FROM preorders WHERE status='waiting'")
    rows = c.fetchall()
    for (kode,) in rows:
        await allocate_preorders(kode)


# pastikan loop auto allocate start saat bot ready
@bot.event
async def on_ready():
    try:
        guild_id = os.getenv("SERVER_ID")
        await bot.tree.sync(guild=discord.Object(id=guild_id))
        print(f"Slash command synced for guild {guild_id}")
    except Exception as e:
        print(f"Gagal sync command: {e}")

    if not auto_allocate_po.is_running():
        auto_allocate_po.start()

    print("[AUTO_ALLOCATE] Loop started")

@bot.event
async def on_message(message: discord.Message):
    """Custom message handler for topup logic and command processing."""
    # Jangan abaikan pesan webhook
    if message.author.bot and message.webhook_id is None:
        return
    if message.channel.id != TARGET_CHANNEL_ID:
        await bot.process_commands(message)
        return
    if (discord.utils.utcnow() - message.created_at).total_seconds() > 5:
        await bot.process_commands(message)
        return
    parts = message.content.split()
    if len(parts) < 2:
        await bot.process_commands(message)
        return
    if message.author.id != 1415979849121796176:
        return
    raw_name = parts[0]
    try:
        amount = int(parts[1])
    except ValueError:
        await bot.process_commands(message)
        return
    growid = re.sub(r'[^a-z0-9]', '', raw_name.lower())
    if growid:
        # cek apakah growid ada di database
        c.execute("SELECT balance, user_id FROM users WHERE nama=?", (growid,))
        row = c.fetchone()
        if row:
            new_balance = row[0] + amount
            c.execute("UPDATE users SET balance = ? WHERE nama=?", (new_balance, growid))
            conn.commit()
            await message.channel.send(
                f"✅ Topup berhasil untuk GrowID **{growid}**\n"
                f"➕ Jumlah : {amount} WL\n"
                f" Saldo sekarang : {new_balance} WL"
            )
            user = await bot.fetch_user(row[1])
            if user:
                await user.send(
                    f"✅ Topup berhasil untuk GrowID **{growid}**\n"
                    f"➕ Jumlah : {amount} WL\n"
                    f" Saldo sekarang : {new_balance} WL"
                )
            print(f"[DEBUG] Added {amount} to {growid} (saldo sekarang {new_balance})")
        else:
            # GrowID belum terdaftar → JANGAN insert
            await message.channel.send(
                f"❌ GrowID **{growid}** belum terdaftar. Minta user klik **SET GROWID** dulu."
            )
        return
    await bot.process_commands(message)

@bot.event
async def on_command_error(ctx, error):
    """Handle command errors gracefully."""
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Kamu tidak punya izin untuk pakai command ini.")
    else:
        # biar error lain tetap muncul di console
        raise error

# Initialize UI views last
ui_views.setup(bot, c, conn, fmt_wl, PREFIX)

if __name__ == "__main__":
    bot.run(BOT_TOKEN)