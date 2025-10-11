"""
Discord Bot (Multi-file) with Full UI
Run this file: python bot_core.py
"""
import discord
from discord.ext import commands
import sqlite3
import re
from dotenv import load_dotenv
import os

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
c.execute("""CREATE TABLE IF NOT EXISTS users (
    nama TEXT PRIMARY KEY,
    balance INTEGER DEFAULT 0
)""")

try:
    c.execute("ALTER TABLE users ADD COLUMN poin INTEGER DEFAULT 0")
except sqlite3.OperationalError:
    pass  # kalau udah ada, abaikan

# Tambah kolom user_id kalau tabel lama belum punya (abaikan error kalau sudah ada)
try:
    c.execute("ALTER TABLE users ADD COLUMN user_id INTEGER")
except sqlite3.OperationalError:
    pass

c.execute("""CREATE TABLE IF NOT EXISTS maintenance (
    is_mt INTEGER DEFAULT 0
)""")

c.execute("INSERT OR IGNORE INTO maintenance (rowid, is_mt) VALUES (1, 0)")

# Buat unique index untuk user_id supaya satu Discord ID tidak nempel ke banyak GrowID
c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)")
conn.commit()
c.execute("""CREATE TABLE IF NOT EXISTS stock (
    kode TEXT PRIMARY KEY,
    judul TEXT,
    harga INTEGER DEFAULT 0
)""")
c.execute("""CREATE TABLE IF NOT EXISTS stock_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kode TEXT,
    nama_barang TEXT
)""")
c.execute("""CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    kode TEXT,
    jumlah INTEGER,
    waktu TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""")

try:
    c.execute("ALTER TABLE transactions ADD COLUMN harga INTEGER")
except sqlite3.OperationalError:
    pass

c.execute("UPDATE transactions SET harga = 10 WHERE harga IS NULL")

conn.commit()

def fmt_wl(x: int) -> str:
    try:
        return f"{int(x):,}".replace(",", ".")
    except Exception:
        return str(x)

# ===== Load modules =====
import cmd_addbal
import cmd_addstock
import cmd_setharga
import cmd_stock
import cmd_buy
import cmd_status
import cmd_info
import cmd_deleteproduct
import cmd_topbal
import cmd_omset
import cmd_track
import cmd_mt
import cmd_addacc
import ui_views
import cmd_ltoken  # ✅ new command LToken real-time API


TARGET_CHANNEL_ID = 1415979811154821170  # ganti dengan ID channel webhook game

@bot.event
async def on_message(message: discord.Message):
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
        # print(row)

        if row:
            new_balance = row[0] + amount
            # new_balance = 1
            c.execute("UPDATE users SET balance = ? WHERE nama=?", (new_balance, growid))
            conn.commit()

            await message.channel.send(
                f"✅ Topup berhasil untuk GrowID **{growid}**\n"
                f"➕ Jumlah : {amount} WL\n"
                f"💰 Saldo sekarang : {new_balance} WL"
            )
            user = await bot.fetch_user(row[1])

            if user:
                await user.send(
                    f"✅ Topup berhasil untuk GrowID **{growid}**\n"
                    f"➕ Jumlah : {amount} WL\n"
                    f"💰 Saldo sekarang : {new_balance} WL"
                )
            print(f"[DEBUG] Added {amount} to {growid} (saldo sekarang {new_balance})")
        else:
            # GrowID belum terdaftar → JANGAN insert
            await message.channel.send(
                f"❌ GrowID **{growid}** belum terdaftar. "
                f"Minta user klik **SET GROWID** dulu."
            )
        return

    await bot.process_commands(message)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Kamu tidak punya izin untuk pakai command ini.")
    else:
        # biar error lain tetap muncul di console
        raise error


# Setup modules
cmd_addbal.setup(bot, c, conn, fmt_wl, PREFIX)
cmd_addstock.setup(bot, c, conn, fmt_wl, PREFIX)
cmd_setharga.setup(bot, c, conn, fmt_wl, PREFIX)
cmd_stock.setup(bot, c, conn, fmt_wl, PREFIX)  # uses UI view
cmd_buy.setup(bot, c, conn, fmt_wl, PREFIX)
cmd_status.setup(bot, c, conn, fmt_wl, PREFIX, DB_NAME)
cmd_info.setup(bot, c, conn, fmt_wl, PREFIX)
cmd_deleteproduct.setup(bot, c, conn, fmt_wl, PREFIX)
cmd_topbal.setup(bot, c, conn, fmt_wl, PREFIX)
cmd_omset.setup(bot, c, conn, fmt_wl, PREFIX)
cmd_track.setup(bot, c, conn, fmt_wl, PREFIX)
cmd_mt.setup(bot, c, conn, fmt_wl, PREFIX)
cmd_addacc.setup(bot. c, conn, fmt_wl, PREFIX)
cmd_ltoken.setup(bot. c, conn, fmt_wl, PREFIX)

# UI must be initialized last so it can hook listeners
ui_views.setup(bot, c, conn, fmt_wl, PREFIX)

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
