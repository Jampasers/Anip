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

# ---------- CONFIG ----------
API_URL = os.getenv("REFRESH_API_URL", "http://23.137.105.146:5050/generate_token")
DEFAULT_PROXY = os.getenv(
    "DEFAULT_PROXY",
    "growtechcentral.com:10000:f44c5d7bf63ce6d4d4ab:c98f897ffef305b0"
)
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "60"))
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PROGRESS_UPDATE_EVERY = 1  # update progress setiap berapa akun
# ----------------------------

def mask_email(email: str) -> str:
    """
    Mask email supaya local part ketutup tapi domain tetap kelihatan.
    """
    try:
        local, domain = email.split("@", 1)
    except Exception:
        return "****@****"
    if len(local) <= 2:
        masked_local = local[0] + "*" * (len(local) - 1)
    else:
        masked_local = local[0] + "*" * (len(local) - 1)
    return f"{masked_local}@{domain}"

async def do_post(api_url: str, payload: dict, headers: dict, timeout: int):
    """
    Jalankan requests.post di thread terpisah supaya tidak block event loop.
    """
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
        description="Masukkan daftar Gmail (dipisah spasi atau enter). Hasil dikirim ke DM buyer."
    )
    @app_commands.describe(
        gmails="Daftar Gmail, contoh: akun1@gmail.com akun2@gmail.com akun3@gmail.com"
    )
    @app_commands.guilds(discord.Object(int(os.getenv("SERVER_ID"))))
    @is_buyer_ltoken()
    async def refresh(self, interaction: discord.Interaction, gmails: str):
        """
        Buyer mengetikkan Gmail langsung (tanpa file).
        Input dipisah spasi atau enter. 
        Setiap Gmail akan diproses ke API_URL dengan proxy default.
        """
        await interaction.response.defer(thinking=True, ephemeral=True)

        # parsing input gmails → list
        raw_lines = gmails.replace("\n", " ").split()
        emails = [ln.strip() for ln in raw_lines if EMAIL_RE.match(ln.strip())]

        if not emails:
            return await interaction.followup.send("❌ Tidak ditemukan Gmail valid di input.", ephemeral=True)

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (DiscordBot)"
        }

        results: List[str] = []
        success_count = 0
        fail_count = 0

        # progress awal
        masked_first = mask_email(emails[0]) if emails else "—"
        total = len(emails)
        progress_msg = await interaction.followup.send(
            f"⏳ Memulai proses refresh `{total}` akun...\n"
            f"Sedang memproses: **{masked_first}**\n"
            f"Sukses: 0 | Gagal: 0",
            ephemeral=True,
            wait=True
        )

        # loop tiap email
        for idx, email in enumerate(emails, start=1):
            masked = mask_email(email)
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

            # update progress message
            if idx % PROGRESS_UPDATE_EVERY == 0 or idx == total:
                try:
                    await progress_msg.edit(content=(
                        f"⏳ Memproses akun {idx}/{total}\n"
                        f"Terakhir diproses: **{masked}**\n\n"
                        f"{status_line}\n\n"
                        f"Sukses: **{success_count}** | Gagal: **{fail_count}**\n"
                        f"Menunggu selesainya proses..."
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
            return await interaction.followup.send(f"❌ Gagal membuat file hasil: `{e}`", ephemeral=True)

        # summary
        summary_text = f"✅ Selesai. Total diproses: {total} — Sukses: {success_count}, Gagal: {fail_count}"

        # DM hasil ke buyer
        user = interaction.user
        dm_sent = False
        try:
            dm_channel = await user.create_dm()
            await dm_channel.send(content=summary_text,
                                  file=discord.File(tmp_path, filename="refreshed_tokens.txt"))
            dm_sent = True
        except Exception:
            dm_sent = False

        try:
            if dm_sent:
                await interaction.followup.send("✅ Hasil sudah dikirim ke DM kamu.", ephemeral=True)
            else:
                await interaction.followup.send(
                    content=f"{summary_text}\n⚠️ Gagal kirim DM — hasil dikirim di sini.",
                    file=discord.File(tmp_path, filename="refreshed_tokens.txt"),
                    ephemeral=True
                )
        except Exception as e:
            await interaction.followup.send(f"❌ Gagal mengirim hasil: `{e}`", ephemeral=True)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

async def setup(bot: commands.Bot):
    await bot.add_cog(RefreshCommand(bot))
