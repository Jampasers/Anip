from discord.ext import commands
import sqlite3

DB_NAME = "discord_sqlite_bot.db"
conn = sqlite3.connect(DB_NAME, check_same_thread=False)
c = conn.cursor()

ALLOWED_ID = ["698127357990404157", "629211566583185408"]

def is_allowed_user():
    def predicate(ctx):
        return str(ctx.author.id) in [u for u in ALLOWED_ID]
    return commands.check(predicate)

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