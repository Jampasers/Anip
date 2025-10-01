# refresh_cog.py
from discord.ext import commands
from discord import app_commands
from utils import is_buyer_ltoken
import os
import discord
import requests
import re
import tempfile

# ---------- CONFIG ----------
# Ubah kalau perlu
API_URL = os.getenv("REFRESH_API_URL", "http://23.137.105.146:5050/generate_token")
DEFAULT_PROXY = os.getenv(
    "DEFAULT_PROXY",
    "growtechcentral.com:10000:f44c5d7bf63ce6d4d4ab:c98f897ffef305b0"
)
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "60"))
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
# ----------------------------

class RefreshCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="refresh",
        description="Upload .txt berisi daftar Gmail (1 per baris). Hasil akan dikirimkan kembali."
    )
    @app_commands.guilds(discord.Object(int(os.getenv("SERVER_ID"))))
    @is_buyer_ltoken()
    async def refresh(self, interaction: discord.Interaction, file: discord.Attachment):
        """
        Buyer uploads a .txt containing emails (1 per line).
        For each email, POST to API_URL with {"email": email, "proxy": DEFAULT_PROXY}
        If API returns {"success": true, "token": "..."} -> write token (clean) to output file.
        Otherwise -> write just the email.
        Finally send the output file to the buyer with a brief summary.
        """
        await interaction.response.defer(thinking=True)

        # validate filename
        if not file.filename.lower().endswith(".txt"):
            return await interaction.followup.send("❌ File harus berekstensi `.txt` (satu Gmail per baris).")

        # read file
        try:
            raw = await file.read()
            text = raw.decode("utf-8", errors="replace").strip()
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        except Exception as e:
            return await interaction.followup.send(f"❌ Gagal membaca file: `{e}`")

        # validate emails
        emails = [ln for ln in lines if EMAIL_RE.match(ln)]
        if not emails:
            return await interaction.followup.send("❌ Tidak ditemukan Gmail valid di file.")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (DiscordBot)"
        }

        results = []
        success_count = 0
        fail_count = 0

        # iterate and call API
        for email in emails:
            payload = {"email": email, "proxy": DEFAULT_PROXY}
            try:
                resp = requests.post(API_URL, headers=headers, json=payload, timeout=TIMEOUT)
            except requests.exceptions.RequestException as e:
                # request-level failure
                results.append(email)
                fail_count += 1
                continue

            # if non-200, treat as fail
            if resp.status_code != 200:
                results.append(email)
                fail_count += 1
                continue

            # try parse json
            try:
                j = resp.json()
            except ValueError:
                results.append(email)
                fail_count += 1
                continue

            # expected shape: {"success": true, "token": "email|..."} (based on your example)
            if j.get("success") and isinstance(j.get("token"), str) and j.get("token").strip():
                # token might already include email|... as you showed. Use it as-is (clean).
                token_value = j["token"].strip()
                results.append(token_value)
                success_count += 1
            else:
                # fallback: maybe some API return token under different key or structure
                # try common keys:
                token_guess = j.get("token") or j.get("data") or j.get("result")
                if token_guess and isinstance(token_guess, str) and "|" in token_guess:
                    results.append(token_guess.strip())
                    success_count += 1
                else:
                    results.append(email)
                    fail_count += 1

        # write output to temporary file
        tmp_path = os.path.join(tempfile.gettempdir(), "refreshed_tokens.txt")
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(results))
        except Exception as e:
            return await interaction.followup.send(f"❌ Gagal membuat file hasil: `{e}`")

        # send file back to buyer with summary
        try:
            summary = f"✅ Selesai. Total: {len(emails)} — Sukses: {success_count}, Gagal: {fail_count}"
            await interaction.followup.send(
                content=summary,
                file=discord.File(tmp_path, filename="refreshed_tokens.txt")
            )
        except Exception as e:
            return await interaction.followup.send(f"❌ Gagal mengirim file hasil: `{e}`")
        finally:
            # cleanup
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(RefreshCommand(bot))
