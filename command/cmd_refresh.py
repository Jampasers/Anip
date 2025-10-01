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
PROGRESS_UPDATE_EVERY = 1  # update UI every N items (set to 1 so user sees each step)
# ----------------------------

def mask_email(email: str) -> str:
    """
    Mask an email so localpart mostly hidden but domain visible.
    Examples:
      anhcaohai28@gmail.com -> a*********@gmail.com
      foo@bar.co -> f**@bar.co
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
    Run requests.post in a thread to avoid blocking event loop.
    Returns (resp_or_exc, is_exception)
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
        description="Upload .txt berisi daftar Gmail (1 per baris). Hasil akan dikirimkan ke DM buyer."
    )
    @app_commands.guilds(discord.Object(int(os.getenv("SERVER_ID"))))
    @is_buyer_ltoken()
    async def refresh(self, interaction: discord.Interaction, file: discord.Attachment):
        """
        Buyer uploads a .txt containing emails (1 per line).
        For each email, POST to API_URL with {"email": email, "proxy": DEFAULT_PROXY}
        If API returns {"success": true, "token": "..."} -> write token (clean) to output file.
        Otherwise -> write just the email.
        Finally send the output file to the buyer via DM with a brief summary.
        """
        await interaction.response.defer(thinking=True)

        # validate filename
        if not file.filename.lower().endswith(".txt"):
            return await interaction.followup.send("❌ File harus berekstensi `.txt` (satu Gmail per baris).", ephemeral=True)

        # read file
        try:
            raw = await file.read()
            text = raw.decode("utf-8", errors="replace").strip()
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        except Exception as e:
            return await interaction.followup.send(f"❌ Gagal membaca file: `{e}`", ephemeral=True)

        # validate emails (keep only valid-looking addresses)
        emails = [ln for ln in lines if EMAIL_RE.match(ln)]
        if not emails:
            return await interaction.followup.send("❌ Tidak ditemukan Gmail valid di file.", ephemeral=True)

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (DiscordBot)"
        }

        results: List[str] = []
        success_count = 0
        fail_count = 0

        # initial progress message (we will edit this)
        masked_first = mask_email(emails[0]) if emails else "—"
        total = len(emails)
        progress_msg = await interaction.followup.send(
            f"⏳ Memulai proses refresh `{total}` akun...\nSedang memproses: **{masked_first}**\nSukses: 0 | Gagal: 0",
            ephemeral=True,
            wait=True
        )

        # iterate and call API (sequentially; can be parallelized if desired)
        for idx, email in enumerate(emails, start=1):
            masked = mask_email(email)
            payload = {"email": email, "proxy": DEFAULT_PROXY}

            # update in-progress every iteration (show masked email)
            try:
                # perform request in background thread
                resp_or_exc, is_exc = await do_post(API_URL, payload, headers, TIMEOUT)
            except Exception as e:
                resp_or_exc = e
                is_exc = True

            # handle response / exception
            if is_exc:
                # network or request error
                results.append(email)  # write only email on failure as requested
                fail_count += 1
                status_line = f"❌ Request error for {masked}"
            else:
                resp = resp_or_exc
                if resp.status_code != 200:
                    results.append(email)
                    fail_count += 1
                    status_line = f"❌ HTTP {resp.status_code} for {masked}"
                else:
                    # parse json safely
                    try:
                        j = resp.json()
                    except Exception:
                        results.append(email)
                        fail_count += 1
                        status_line = f"❌ Invalid JSON for {masked}"
                    else:
                        # expected shape: {"success": true, "token": "email|..."}
                        if j.get("success") and isinstance(j.get("token"), str) and j.get("token").strip():
                            token_value = j["token"].strip()
                            results.append(token_value)
                            success_count += 1
                            status_line = f"✅ Berhasil: {masked}"
                        else:
                            # fallback attempt to find token-like field
                            token_guess = j.get("token") or j.get("data") or j.get("result")
                            if token_guess and isinstance(token_guess, str) and "|" in token_guess:
                                results.append(token_guess.strip())
                                success_count += 1
                                status_line = f"✅ Berhasil (guessed): {masked}"
                            else:
                                results.append(email)
                                fail_count += 1
                                status_line = f"❌ Gagal (API) for {masked}"

            # periodically update the progress message
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
                    # editing may fail (rare) — ignore and continue
                    pass

        # write output to temporary file (unique)
        try:
            tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", prefix="refreshed_tokens_", suffix=".txt")
            tmp_path = tmp.name
            tmp.write("\n".join(results))
            tmp.close()
        except Exception as e:
            return await interaction.followup.send(f"❌ Gagal membuat file hasil: `{e}`", ephemeral=True)

        # final summary text
        summary_text = f"✅ Selesai. Total diproses: {total} — Sukses: {success_count}, Gagal: {fail_count}"

        # Try to DM the buyer the result file
        user = interaction.user
        dm_sent = False
        try:
            dm_channel = await user.create_dm()
            await dm_channel.send(content=summary_text, file=discord.File(tmp_path, filename="refreshed_tokens.txt"))
            dm_sent = True
        except Exception:
            dm_sent = False

        # notify in channel (ephemeral) about result and where file is
        try:
            if dm_sent:
                await interaction.followup.send("✅ Hasil sudah dikirim ke DM kamu. Periksa pesan langsung (DM).", ephemeral=True)
            else:
                # fallback: attach file to followup (ephemeral)
                await interaction.followup.send(
                    content=f"{summary_text}\n⚠️ Gagal kirim DM — mengirim file hasil di sini (ephemeral).",
                    file=discord.File(tmp_path, filename="refreshed_tokens.txt"),
                    ephemeral=True
                )
        except Exception as e:
            # Both DM and followup failed
            await interaction.followup.send(f"❌ Gagal mengirim hasil: `{e}`. Hubungi admin.", ephemeral=True)
        finally:
            # cleanup temp file
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

async def setup(bot: commands.Bot):
    await bot.add_cog(RefreshCommand(bot))
