from discord.ext import commands
import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()

DB_NAME = "discord_sqlite_bot.db"
conn = sqlite3.connect(DB_NAME, check_same_thread=False)
c = conn.cursor()

ROLE_BUY = int(os.getenv("ROLE_BUY", "0"))


def _is_server_admin(author) -> bool:
    perms = getattr(author, "guild_permissions", None)
    return bool(perms and perms.administrator)

def is_allowed_user():
    def predicate(ctx):
        return _is_server_admin(ctx.author)
    return commands.check(predicate)

from discord import app_commands, Interaction
def is_buyer_ltoken():
    def predicate(interaction: Interaction):
        return any(role.id == ROLE_BUY for role in interaction.user.roles)
    return app_commands.check(predicate)



def is_maintenance():
    async def predicate(ctx):
        if _is_server_admin(ctx.author):
            return True
        
        c.execute("SELECT is_mt FROM maintenance LIMIT 1")
        row = c.fetchone()
        is_mt = row[0] if row else 0
        if is_mt:
            await ctx.send("⚠️ Bot sedang dalam mode maintenance. Silakan coba lagi nanti.")
            return False

        return True
    return commands.check(predicate)
