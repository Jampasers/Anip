from discord.ext import commands
import discord

def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register the info command."""
    @bot.command(usage=f"{PREFIX}info [@user | growid]")
    async def info(ctx, arg: str = None):
        growid = None
        balance = None
        poin = None
        # 1) !info → profil diri sendiri
        if arg is None:
            c.execute("SELECT nama, balance, poin FROM users WHERE user_id =  ?", (ctx.author.id,))
            row = c.fetchone()
            if row:
                growid, balance, poin = row
                target_display = ctx.author.mention
            else:
                await ctx.send("You are not registered.")
                return
        elif len(ctx.message.mentions) > 0:
            # 2) !info @orang → profil user yang ditag
            member = ctx.message.mentions[0]
            c.execute("SELECT nama, balance, poin FROM users WHERE user_id =  ?", (member.id,))
            row = c.fetchone()
            if row:
                growid, balance, poin = row
                target_display = member.mention
            else:
                await ctx.send(f"{member.mention} is not registered.")
                return
        else:
            # 3) !info GrowID → cari langsung di kolom nama
            c.execute("SELECT nama, balance, poin FROM users WHERE nama = ?", (arg,))
            row = c.fetchone()
            if row:
                growid, balance, poin = row
                target_display = growid
            else:
                await ctx.send(f"GrowID `{arg}` not found in database.")
                return
        # --- Kirim hasil pakai embed
        embed = discord.Embed(title=" Profile", color=discord.Color.blue())
        embed.add_field(name="GrowID", value=growid, inline=True)
        embed.add_field(name="Balance", value=fmt_wl(balance), inline=True)
        embed.add_field(name="Point", value=poin, inline=True)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(f"Profile info for {target_display}", embed=embed)
