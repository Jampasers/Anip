import psutil
import time
from discord.ext import commands
from discord import app_commands
import os

def setup(bot, c, conn, fmt_wl, PREFIX, DB_NAME):
    """Register the status command."""
    start_time = time.time()

    @app_commands.guilds(os.getenv("SERVER_ID"))
    # @bot.command(usage=f"{PREFIX}status")
    @bot.hybrid_command(name="status",
                        usage=f"{PREFIX}status",
                        description="Check status hosting, bot, dll")
    async def status(ctx):
        ping_ms = round(bot.latency * 1000)
        signal = "Good" if ping_ms < 100 else "Mid" if ping_ms < 200 else "Bad" if ping_ms < 400 else ""
        mem = psutil.virtual_memory()
        ram_total = round(mem.total / (1024 ** 3), 2)
        ram_used = round(mem.used / (1024 ** 3), 2)
        ram_percent = mem.percent
        cpu_percent = psutil.cpu_percent(interval=1)
        uptime_seconds = int(time.time() - start_time)
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        await ctx.send(
            f"``` Status\n"
            f"-----------------------------\n"
            f"Ping     : {ping_ms} ms {signal}\n"
            f"CPU      : {cpu_percent}%\n"
            f"RAM      : {ram_used}/{ram_total} GB ({ram_percent}%)\n"
            f"Uptime   : {hours}h {minutes}m {seconds}s\n"
            f"DB File  : {DB_NAME}\n```"
        )
