# ================================================================
# refresh_cog.py — FINAL FULL VERSION (🔥 Ultra Complete)
# ================================================================
# FUNGSI UTAMA:
# /refresh (upload file .txt)
# - Membaca setiap baris token dari file upload
# - Tiap token → refresh via SurferCID API resmi
# - Potong saldo user 1 WL per token
# - Jika gagal refresh → rollback 1 WL
# - Jika saldo tidak cukup → token di-skip
# - Hasil dikirim ke DM:
#     refreshed_success.txt = token berhasil
#     refreshed_failed.txt  = token gagal / saldo kurang
# ================================================================

from discord.ext import commands
from discord import app_commands
import discord
import os
import sqlite3
import asyncio
from typing import List, Tuple
import tempfile

# SurferCID resmi
from surfercid import SurferCIDClient
from surfercid.models import LTokenAccount

# ================================================================
# KONFIGURASI ENVIRONMENT
# ================================================================
SURFERCID_API_KEY = os.getenv("SURFERCID_API_KEY") or os.getenv("LTOKEN_API_KEY") or ""
DB_PATH = os.getenv("DB_PATH", "discord_sqlite_bot.db")
MAX_WORKERS = int(os.getenv("REFRESH_MAX_WORKERS", "5"))
SERVER_ID = int(os.getenv("SERVER_ID") or "0")

# ================================================================
# DATABASE UTILITIES
# ================================================================
def db_connect():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def db_get_user_balance(conn: sqlite3.Connection, user_id: int) -> int:
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0

def db_debit(conn: sqlite3.Connection, user_id: int, amount: int) -> bool:
    """Kurangi saldo user; return True jika berhasil, False jika saldo kurang."""
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    bal = int(row[0]) if row and row[0] is not None else 0
    if bal < amount:
        return False
    new_bal = bal - amount
    cur.execute("UPDATE users SET balance=? WHERE user_id=?", (new_bal, user_id))
    conn.commit()
    return True

def db_credit(conn: sqlite3.Connection, user_id: int, amount: int):
    """Rollback saldo user."""
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    bal = int(row[0]) if row and row[0] is not None else 0
    cur.execute("UPDATE users SET balance=? WHERE user_id=?", (bal + amount, user_id))
    conn.commit()

# ================================================================
# WORKER UNTUK REFRESH TOKEN
# ================================================================
def _refresh_one_token(token_str: str, api_key: str) -> Tuple[bool, str]:
    """
    Jalankan refresh 1 token.
    Return:
      (True, refreshed_token)   jika sukses
      (False, original_token)   jika gagal
    """
    token_str = token_str.strip()
    if not token_str:
        return False, "EMPTY_LINE"

    try:
        account = LTokenAccount.from_format(token_str)
        client = SurferCIDClient(api_key=api_key)
        refreshed = client.refresh_token(account)
        return True, refreshed.to_format()
    except Exception:
        return False, token_str

# ================================================================
# MAIN CLASS COG
# ================================================================
class RefreshFileCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="refresh",
        description="Upload file .txt berisi token (1 token per baris) untuk di-refresh."
    )
    @app_commands.describe(file="File .txt berisi token")
    @app_commands.guilds(discord.Object(SERVER_ID) if SERVER_ID else None)
    async def refresh(self, interaction: discord.Interaction, file: discord.Attachment):
        """Handler utama untuk perintah /refresh"""
        if not SURFERCID_API_KEY:
            await interaction.response.send_message("❌ SURFERCID_API_KEY belum diatur di .env", ephemeral=True)
            return

        if not file.filename.lower().endswith(".txt"):
            await interaction.response.send_message("❌ Harap upload file .txt", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            content = (await file.read()).decode("utf-8", errors="ignore")
        except Exception as e:
            await interaction.followup.send(f"❌ Gagal membaca file: {e}")
            return

        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        if not lines:
            await interaction.followup.send("❌ File kosong / tidak ada token valid.")
            return

        conn = db_connect()
        user_id = interaction.user.id
        saldo_awal = db_get_user_balance(conn, user_id)

        progress = await interaction.followup.send(
            f"⏳ Memulai refresh {len(lines)} token...\n"
            f"Saldo awal: {saldo_awal} WL\n"
            f"Berjalan: 0/{len(lines)} | ✅ 0 | ❌ 0 | ⚠️ 0"
        )

        success_tokens: List[str] = []
        failed_tokens: List[str] = []
        success, fail, skip = 0, 0, 0
        total = len(lines)

        loop = asyncio.get_event_loop()
        sem = asyncio.Semaphore(MAX_WORKERS)

        async def process_token(token_line: str) -> Tuple[bool, str]:
            """Proses 1 token: refresh (GRATIS)"""
            # if not db_debit(conn, user_id, 1):
            #    return None, "SALDO_KURANG"
            
            def work(): return _refresh_one_token(token_line, SURFERCID_API_KEY)
            ok, result = await loop.run_in_executor(None, work)
            
            if ok:
                return True, result
            else:
                # db_credit(conn, user_id, 1) # Tidak perlu credit balik karena gratis
                return False, result

        async def sem_task(line):
            async with sem:
                return await process_token(line)

        tasks = [asyncio.create_task(sem_task(ln)) for ln in lines]

        for coro in asyncio.as_completed(tasks):
            res = await coro
            if res[0] is True:
                success += 1
                success_tokens.append(res[1])
            elif res[0] is False:
                fail += 1
                failed_tokens.append(res[1])
            else:
                skip += 1
                failed_tokens.append(res[1])

            done = success + fail + skip
            try:
                await progress.edit(content=(
                    f"⏳ Progress: {done}/{total}\n"
                    f"✅ {success} | ❌ {fail} | ⚠️ {skip}\n"
                    f"Saldo sekarang: {db_get_user_balance(conn, user_id)} WL"
                ))
            except Exception:
                pass

        # ===============================
        # Simpan hasil ke file sementara
        # ===============================
        tmp_success = None
        tmp_failed = None
        try:
            if success_tokens:
                fs = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8",
                                                 prefix="refreshed_success_", suffix=".txt")
                fs.write("\n".join(success_tokens))
                fs.close()
                tmp_success = fs.name
            if failed_tokens:
                ff = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8",
                                                 prefix="refreshed_failed_", suffix=".txt")
                ff.write("\n".join(failed_tokens))
                ff.close()
                tmp_failed = ff.name
        except Exception as e:
            await progress.edit(content=f"❌ Gagal menulis file hasil: {e}")
            conn.close()
            return

        # ===============================
        # Kirim hasil ke DM user
        # ===============================
        summary = (
            f"✅ Selesai refresh {total} token.\n"
            f"Sukses: {success} | Gagal: {fail} | Skip saldo kurang: {skip}\n"
            f"Saldo akhir: {db_get_user_balance(conn, user_id)} WL"
        )

        dm_ok = False
        try:
            dm = await interaction.user.create_dm()
            files = []
            if tmp_success:
                files.append(discord.File(tmp_success, filename="refreshed_success.txt"))
            if tmp_failed:
                files.append(discord.File(tmp_failed, filename="refreshed_failed.txt"))
            if files:
                await dm.send(content=summary, files=files)
            else:
                await dm.send(content=summary)
            dm_ok = True
        except Exception:
            dm_ok = False

        # ===============================
        # Update progress terakhir di server
        # ===============================
        try:
            if dm_ok:
                await progress.edit(content=f"{summary}\n✅ Hasil dikirim ke DM kamu.")
            else:
                files = []
                if tmp_success:
                    files.append(discord.File(tmp_success, filename="refreshed_success.txt"))
                if tmp_failed:
                    files.append(discord.File(tmp_failed, filename="refreshed_failed.txt"))
                if files:
                    await progress.edit(content=f"{summary}\n⚠️ Gagal DM, hasil dikirim di sini.")
                    await interaction.followup.send(files=files)
                else:
                    await progress.edit(content=f"{summary}\n⚠️ Gagal DM.")
        finally:
            # Bersih-bersih
            for f in [tmp_success, tmp_failed]:
                if f and os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception:
                        pass
            conn.close()

# ================================================================
# REGISTER COG
# ================================================================
async def setup(bot: commands.Bot):
    await bot.add_cog(RefreshFileCog(bot))
