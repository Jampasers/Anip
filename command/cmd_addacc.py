from discord.ext import commands
import sqlite3
import os
import re

DB_PATH = os.getenv("DB_PATH", "discord_sqlite_bot.db")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

class AddAcc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    @commands.command(name="addacc")
    async def addacc(self, ctx: commands.Context, emails: str):
        """
        Tambah satu atau banyak akun email ke database.
        Format: !addacc email1,email2,email3
        """
        email_list = [e.strip() for e in emails.split(",") if e.strip()]
        if not email_list:
            return await ctx.send("❌ Tidak ada email valid di input.")

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        success = []
        duplicate = []
        invalid = []

        for email in email_list:
            if not EMAIL_RE.match(email):
                invalid.append(email)
                continue
            try:
                cur.execute("INSERT OR IGNORE INTO accounts(email) VALUES (?)", (email,))
                if cur.rowcount == 0:
                    duplicate.append(email)
                else:
                    success.append(email)
            except Exception:
                invalid.append(email)

        conn.commit()
        conn.close()

        # buat summary message
        msg = []
        if success:
            msg.append("✅ Ditambahkan: " + ", ".join(success))
        if duplicate:
            msg.append("⚠️ Sudah ada: " + ", ".join(duplicate))
        if invalid:
            msg.append("❌ Invalid: " + ", ".join(invalid))

        await ctx.send("\n".join(msg) if msg else "⚠️ Tidak ada akun berhasil ditambahkan.")

async def setup(bot: commands.Bot):
    await bot.add_cog(AddAcc(bot))
