from discord.ext import commands
from discord import app_commands
import discord
import os

def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register the info command."""
    @bot.hybrid_command(name="info",
                        usage=f"{PREFIX}info [@user | growid]",
                        description="Tampilkan profil user berdasarkan mention atau GrowID")
    @app_commands.describe(
        member="(Opsional) Mention user Discord",
        growid="(Opsional) GrowID yang ingin dicari"
    )
    @app_commands.guilds(os.getenv("SERVER_ID"))
    async def info(ctx,
                   member: discord.User = None,
                   growid: str = None):
        """
        - Prefix: !info → info diri sendiri, !info @user → info user, !info GROWID → info GrowID
        - Slash: /info member:@user → info user, /info growid:ABC → info GrowID
        """
        # Gunakan context invoker sebagai default
        target_user = ctx.author
        target_growid = None

        # Jika dipanggil via slash, ctx.interaction akan terisi
        # Cek parameter untuk mode slash
        if member:
            # Cari berdasarkan user ID
            c.execute("SELECT nama, balance, poin FROM users WHERE user_id = ?", (member.id,))
            row = c.fetchone()
            if row:
                target_growid, balance, poin = row
                target_display = member.mention
            else:
                await ctx.send(f"{member.mention} belum terdaftar.")
                return
        elif growid:
            # Cari berdasarkan GrowID string
            c.execute("SELECT nama, balance, poin FROM users WHERE nama = ?", (growid,))
            row = c.fetchone()
            if row:
                target_growid, balance, poin = row
                target_display = growid
            else:
                await ctx.send(f"GrowID `{growid}` tidak ditemukan.")
                return
        else:
            # Prefix tanpa argumen atau slash tanpa opsi: tampilkan info invoker
            c.execute("SELECT nama, balance, poin FROM users WHERE user_id = ?", (ctx.author.id,))
            row = c.fetchone()
            if row:
                target_growid, balance, poin = row
                target_display = ctx.author.mention
            else:
                await ctx.send("Kamu belum terdaftar.")
                return

        # Kirim embed hasil
        embed = discord.Embed(title="Profile", color=discord.Color.blue())
        embed.add_field(name="GrowID", value=target_growid, inline=True)
        embed.add_field(name="Balance", value=fmt_wl(balance), inline=True)
        embed.add_field(name="Point", value=poin, inline=True)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(f"Profile info for {target_display}", embed=embed)
