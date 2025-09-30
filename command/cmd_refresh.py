from discord.ext import commands
from discord import app_commands
from utils import is_allowed_user
from utils import is_buyer_ltoken
import os
import discord
import requests
import json

class RefreshCommand(commands.Cog):
    @app_commands.command(name="refresh", description="Refresh ltoken dari file .txt")
    @app_commands.guilds(discord.Object(os.getenv("SERVER_ID")))
    @is_buyer_ltoken()
    async def refresh(self, interaction: discord.Interaction, file: discord.Attachment):
        await interaction.response.defer(thinking=True)

        # Validasi format file
        if not file.filename.endswith(".txt"):
            return await interaction.followup.send("❌ File harus berformat `.txt`")

        emails = None
        # Baca isi file
        try:
            content = await file.read()
            emails_raw = content.decode("utf-8").strip()
            emails = emails_raw.splitlines()
        except Exception as e:
            return await interaction.followup.send(f"❌ Gagal baca file: `{e}`")

        # Payload dan headers
        url = "http://23.137.105.22:8000/api/process"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
            "Referer": "http://23.137.105.22:8000/"
        }
        payload = {
            "emails": emails,
            "threads": 50,
            "use_custom_proxy": False,
            "custom_proxies": None
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            return await interaction.followup.send(f"✅ Status: {resp.status_code}\n```json\n{json.dumps(result, indent=2)}```")
        except requests.exceptions.RequestException as e:
            return await interaction.followup.send(f"❌ Gagal request: `{e}`")
        except ValueError:
            return await interaction.followup.send(f"❌ Gagal parse response: `{resp.text}`")