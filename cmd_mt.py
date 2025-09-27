from discord.ext import commands
from utils import is_allowed_user

def setup(bot, c, conn, fmt_wl, PREFIX):

    @bot.command(usage=f"{PREFIX}mt")
    @is_allowed_user()
    async def mt(ctx):
        # Toggle nilai is_mt
        c.execute("UPDATE maintenance SET is_mt = 1 - is_mt")

        # Ambil nilai setelah update
        c.execute("SELECT is_mt FROM maintenance LIMIT 1")
        row = c.fetchone()

        # Pastikan data valid
        if row is not None:
            status = "🛠️ Maintenance Aktif!" if row[0] == 1 else "✅ Maintenance Nonaktif."
            await ctx.send(f"```{status}```")
        else:
            await ctx.send("```❌ Tidak ada data di tabel maintenance.```")
            
        conn.commit()
