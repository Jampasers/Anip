# refresh_cog.py
from discord.ext import commands
from discord import app_commands
from utils import is_buyer_ltoken
import os
import discord
import requests
import re
import tempfile
import asyncio
from typing import List
import sqlite3
import time

# ---------- CONFIG ----------
API_URL = os.getenv("REFRESH_API_URL", "http://23.137.105.146:5050/generate_token")
DEFAULT_PROXY = os.getenv(
    "DEFAULT_PROXY",
    "growtechcentral.com:10000:f44c5d7bf63ce6d4d4ab:c98f897ffef305b0"
)
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "60"))
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PROGRESS_UPDATE_EVERY = 1
DB_PATH = os.getenv("DB_PATH", "discord_sqlite_bot.db")
# ----------------------------

def account_exists(email: str) -> bool:
    """Cek apakah email ada di DB"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("SELECT 1 FROM accounts WHERE email = ?", (email,))
    row = cur.fetchone()
    conn.close()
    return row is not None

def mask_email(email: str) -> str:
    try:
        local, domain = email.split("@", 1)
    except Exception:
        return "****@****"
    return local[0] + "*" * (len(local) - 1) + "@" + domain

async def do_post(api_url: str, payload: dict, headers: dict, timeout: int):
    def _sync_post():
        return requests.post(api_url, headers=headers, json=payload, timeout=timeout)

    try:
        resp = await asyncio.to_thread(_sync_post)
        return resp, False
    except Exception as e:
        return e, True

class RefreshCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="refresh",
        description="Masukkan daftar Gmail (dipisah spasi/enter). Hasil dikirim ke DM buyer."
    )
    @app_commands.describe(
        gmails="Daftar Gmail, contoh: akun1@gmail.com akun2@gmail.com akun3@gmail.com"
    )
    @app_commands.guilds(discord.Object(int(os.getenv("SERVER_ID"))))
    @is_buyer_ltoken()
    async def refresh(self, interaction: discord.Interaction, gmails: str):
        # response awal → non-ephemeral supaya semua orang bisa lihat
        await interaction.response.defer(thinking=True)

        raw_lines = gmails.replace("\n", " ").split()
        emails = [ln.strip() for ln in raw_lines if EMAIL_RE.match(ln.strip())]

        if not emails:
            return await interaction.followup.send("❌ Tidak ditemukan Gmail valid di input.")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (DiscordBot)"
        }

        results: List[str] = []
        success_count = 0
        fail_count = 0
        notfound_count = 0

        total = len(emails)
        masked_first = mask_email(emails[0]) if emails else "—"
        start_time = time.time()

        # satu pesan awal
        progress_msg = await interaction.followup.send(
            f"⏳ Memulai proses refresh `{total}` akun...\n"
            f"Sedang memproses: **{masked_first}**\n"
            f"Sukses: 0 | Gagal: 0 | NotFound: 0\n"
            f"Waktu berjalan: 0 detik"
        )

        # loop tiap email
        for idx, email in enumerate(emails, start=1):
            masked = mask_email(email)

            if not account_exists(email):
                results.append(f"{email} | NOT_FOUND")
                notfound_count += 1
                status_line = f"❌ Account not found: {masked}"
            else:
                payload = {"email": email, "proxy": DEFAULT_PROXY}
                try:
                    resp_or_exc, is_exc = await do_post(API_URL, payload, headers, TIMEOUT)
                except Exception as e:
                    resp_or_exc = e
                    is_exc = True

                if is_exc:
                    results.append(email)
                    fail_count += 1
                    status_line = f"❌ Request error untuk {masked}"
                else:
                    resp = resp_or_exc
                    if resp.status_code != 200:
                        results.append(email)
                        fail_count += 1
                        status_line = f"❌ HTTP {resp.status_code} untuk {masked}"
                    else:
                        try:
                            j = resp.json()
                        except Exception:
                            results.append(email)
                            fail_count += 1
                            status_line = f"❌ JSON tidak valid untuk {masked}"
                        else:
                            if j.get("success") and isinstance(j.get("token"), str) and j.get("token").strip():
                                token_value = j["token"].strip()
                                results.append(token_value)
                                success_count += 1
                                status_line = f"✅ Berhasil: {masked}"
                            else:
                                token_guess = j.get("token") or j.get("data") or j.get("result")
                                if token_guess and isinstance(token_guess, str) and "|" in token_guess:
                                    results.append(token_guess.strip())
                                    success_count += 1
                                    status_line = f"✅ Berhasil (guessed): {masked}"
                                else:
                                    results.append(email)
                                    fail_count += 1
                                    status_line = f"❌ Gagal API untuk {masked}"

            # update progress di 1 pesan
            if idx % PROGRESS_UPDATE_EVERY == 0 or idx == total:
                elapsed = int(time.time() - start_time)
                try:
                    await progress_msg.edit(content=(
                        f"⏳ Memproses akun {idx}/{total}\n"
                        f"Terakhir diproses: **{masked}**\n\n"
                        f"{status_line}\n\n"
                        f"Sukses: **{success_count}** | "
                        f"Gagal: **{fail_count}** | "
                        f"NotFound: **{notfound_count}**\n"
                        f"Waktu berjalan: {elapsed} detik"
                    ))
                except Exception:
                    pass

        # tulis hasil ke file
        try:
            tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8",
                                              prefix="refreshed_tokens_", suffix=".txt")
            tmp_path = tmp.name
            tmp.write("\n".join(results))
            tmp.close()
        except Exception as e:
            return await progress_msg.edit(content=f"❌ Gagal membuat file hasil: `{e}`")

        summary_text = (
            f"✅ Selesai. Total diproses: {total}\n"
            f"Sukses: {success_count} | Gagal: {fail_count} | NotFound: {notfound_count}\n"
            f"Waktu berjalan: {int(time.time() - start_time)} detik"
        )

        # DM hasil
        user = interaction.user
        dm_sent = False
        try:
            dm_channel = await user.create_dm()
            await dm_channel.send(content=summary_text,
                                  file=discord.File(tmp_path, filename="refreshed_tokens.txt"))
            dm_sent = True
        except Exception:
            dm_sent = False

        # Update pesan progress terakhir
        try:
            if dm_sent:
                await progress_msg.edit(content=f"{summary_text}\n✅ Hasil sudah dikirim ke DM kamu.")
            else:
                await progress_msg.edit(
                    content=f"{summary_text}\n⚠️ Gagal kirim DM — hasil dikirim di sini.",
                    attachments=[discord.File(tmp_path, filename="refreshed_tokens.txt")]
                )
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

async def setup(bot: commands.Bot):
    await bot.add_cog(RefreshCommand(bot))
