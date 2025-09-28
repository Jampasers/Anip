from discord.ext import commands
import discord
from utils import is_allowed_user   

def setup(bot, c, conn, fmt_wl, PREFIX):

    def normalize_name(s: str) -> str:
        import re
        return re.sub(r'[^a-z0-9]', '', (s or '').lower())

    @bot.command(name="addbal", usage=f"{PREFIX}addbal <growid/@user> <amount>")
    @is_allowed_user()
    async def addbal(ctx, target: str, amount: int):
        # Cek jika yang dikasih adalah tag user
        if ctx.message.mentions:
            user = ctx.message.mentions[0]
            user_id = str(user.id)

            # Langsung update berdasarkan user ID, tanpa cek GrowID
            c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            if not row:
                await ctx.send("❌ User belum terdaftar.")
                return

            current = int(row[0] or 0)
            new_balance = current + amount

            c.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
            conn.commit()

            sign = "+" if amount > 0 else ""
            await ctx.send(
                "```💰 Balance Updated (via User)\n"
                "--------------------------\n"
                f"User   : {user.name}\n"
                f"Change : {sign}{fmt_wl(amount)} WL\n"
                f"Before : {fmt_wl(current)} WL\n"
                f"After  : {fmt_wl(new_balance)} WL```"
            )
            return
        
        # Kalau bukan mention user, anggap GrowID manual
        g = normalize_name(target)

        if not g:
            await ctx.send("❌ GrowID tidak valid. Gunakan huruf/angka saja.")
            return

        if amount == 0:
            await ctx.send("❌ Amount tidak boleh 0.")
            return

        c.execute("SELECT balance FROM users WHERE nama = ?", (g,))
        row = c.fetchone()
        if not row:
            await ctx.send(f"❌ GrowID `{g}` belum terdaftar. Minta user klik **SET GROWID** dulu.")
            return

        current = int(row[0] or 0)
        new_balance = current + amount

        c.execute("UPDATE users SET balance = ? WHERE nama = ?", (new_balance, g))
        conn.commit()

        sign = "+" if amount > 0 else ""
        await ctx.send(
            "```💰 Balance Updated (via GrowID)\n"
            "--------------------------\n"
            f"GrowID : {g}\n"
            f"Change : {sign}{fmt_wl(amount)} WL\n"
            f"Before : {fmt_wl(current)} WL\n"
            f"After  : {fmt_wl(new_balance)} WL```"
        )
