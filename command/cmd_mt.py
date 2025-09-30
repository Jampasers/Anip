from discord.ext import commands
from discord import app_commands
from utils import is_allowed_user
import os

def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register the maintenance toggle command."""
    # @bot.command(usage=f"{PREFIX}mt")
    @bot.hybrid_command(name="mt", usage=f"{PREFIX}mt", description="Toggle maintenance mode")
    @is_allowed_user()
    @app_commands.guilds(os.getenv("SERVER_ID"))
    async def mt(ctx):
        # Toggle nilai is_mt
        c.execute("UPDATE maintenance SET is_mt = 1 - is_mt")
        # Ambil nilai setelah update
        c.execute("SELECT is_mt FROM maintenance LIMIT 1")
        row = c.fetchone()
        # Pastikan data valid
        if row is not None:
            status = "️ Maintenance Aktif!" if row[0] == 1 else "✅ Maintenance Nonaktif."
            await ctx.send(f"```{status}```")
        else:
            await ctx.send("```❌ Tidak ada data di tabel maintenance.```")
        conn.commit()
