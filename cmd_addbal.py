from discord.ext import commands
from utils import is_allowed_user, is_maintenance

def setup(bot, c, conn, fmt_wl, PREFIX):

    def normalize_name(s: str) -> str:
        import re
        return re.sub(r'[^a-z0-9]', '', (s or '').lower())

    @bot.command(name="addbal", usage=f"{PREFIX}addbal <growid> <amount>")
    @is_allowed_user() #hanya user di ALLOWED_USERNAMES
    @is_maintenance()
    async def addbal(ctx, growid: str, amount: int):
        # Normalisasi growid: lowercase, hanya a-z0-9
        g = normalize_name(growid)

        if not g:
            await ctx.send("❌ GrowID tidak valid. Gunakan huruf/angka saja.")
            return

        # amount boleh negatif (untuk koreksi), nol ditolak
        if amount == 0:
            await ctx.send("❌ Amount tidak boleh 0.")
            return

        # Cari user berdasarkan nama (GrowID)
        c.execute("SELECT balance FROM users WHERE nama = ?", (g,))
        row = c.fetchone()
        if not row:
            await ctx.send(f"❌ GrowID `{g}` belum terdaftar. Minta user klik **SET GROWID** dulu.")
            return

        current = int(row[0] or 0)
        new_balance = current + int(amount)

        c.execute("UPDATE users SET balance = ? WHERE nama = ?", (new_balance, g))
        conn.commit()

        sign = "+" if amount > 0 else ""
        await ctx.send(
            "```💰 Balance Updated\n"
            "--------------------------\n"
            f"GrowID : {g}\n"
            f"Change : {sign}{fmt_wl(amount)} WL\n"
            f"Before : {fmt_wl(current)} WL\n"
            f"After  : {fmt_wl(new_balance)} WL```"
        )
