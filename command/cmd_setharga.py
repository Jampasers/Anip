from discord.ext import commands
from utils import is_allowed_user, is_maintenance
from discord import app_commands
import os
import discord

def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register the setharga command."""
    # @bot.command(usage=f"{PREFIX}setharga <code> <price>")
    @bot.hybrid_command(name="setharga",
                        usage=f"{PREFIX}setharga <code> <price>",
                        description="Set harga product")
    @is_allowed_user()  # hanya user di
    @is_maintenance()
    @app_commands.guilds(discord.Object(os.getenv("SERVER_ID")))
    async def setharga(ctx, code: str, price: int):
        c.execute("SELECT 1 FROM stock WHERE kode = ?", (code,))
        if not c.fetchone():
            await ctx.send(f"Code {code} not found.")
            return
        c.execute("UPDATE stock SET harga = ? WHERE kode = ?", (price, code))
        conn.commit()
        await ctx.send(
            f"```️ Price Updated\n"
            f"--------------------------\n"
            f"Code  : {code}\n"
            f"Price : {fmt_wl(price)} WL```"
        )
