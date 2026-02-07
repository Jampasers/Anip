from discord.ext import commands
import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()

DB_NAME = "discord_sqlite_bot.db"
conn = sqlite3.connect(DB_NAME, check_same_thread=False)
c = conn.cursor()

# Load from .env
ALLOWED_ID = os.getenv("ALLOWED_ADMIN_IDS", "").split(",")
ROLE_BUY = int(os.getenv("ROLE_BUY", "0"))

def is_allowed_user():
    def predicate(ctx):
        return str(ctx.author.id) in [u for u in ALLOWED_ID]
    return commands.check(predicate)

from discord import app_commands, Interaction
def is_buyer_ltoken():
    def predicate(interaction: Interaction):
        return any(role.id == ROLE_BUY for role in interaction.user.roles)
    return app_commands.check(predicate)



def is_maintenance():
    async def predicate(ctx):
        if str(ctx.author.id) in [u for u in ALLOWED_ID]:
            return True
        
        c.execute("SELECT is_mt FROM maintenance LIMIT 1")
        row = c.fetchone()
        is_mt = row[0] if row else 0
        if is_mt:
            await ctx.send("⚠️ Bot sedang dalam mode maintenance. Silakan coba lagi nanti.")
            return False

        return True
    return commands.check(predicate)