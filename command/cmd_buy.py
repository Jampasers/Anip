from discord.ext import commands
from discord import app_commands
from utils import is_allowed_user, is_maintenance
import os
import discord

def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register the buy command."""
    @is_allowed_user()  # hanya user di ALLOWED_USERNAMES
    @is_maintenance()
    @app_commands.guilds(discord.Object(os.getenv("SERVER_ID")))
    # @bot.command(usage=f"{PREFIX}buy <code> <amount>")
    @bot.hybrid_command(name="buy",
                        usage=f"{PREFIX}buy <code> <amount>",
                        description="Buy product from the bot")
    async def buy(ctx, code: str, amount: int):
        uid = ctx.author.id
        c.execute("SELECT balance FROM users WHERE user_id = ?", (uid,))
        row = c.fetchone()
        if not row:
            await ctx.send("You are not registered.")
            return
        balance = row[0]
        c.execute("SELECT COUNT(*) FROM stock_items WHERE kode = ?", (code,))
        current_stock = c.fetchone()[0]
        if current_stock == 0 or amount > current_stock:
            await ctx.send("Not enough stock.")
            return
        c.execute("SELECT harga FROM stock WHERE kode = ?", (code,))
        r = c.fetchone()
        if not r:
            await ctx.send("Invalid code.")
            return
        price = r[0]
        total = price * amount
        if balance < total:
            await ctx.send("Insufficient balance.")
            return
        c.execute(
            "SELECT id, nama_barang FROM stock_items WHERE kode = ? ORDER BY id LIMIT ?",
            (code, amount),
        )
        items = c.fetchall()
        ids = [str(i[0]) for i in items]
        c.execute(f"DELETE FROM stock_items WHERE id IN ({','.join(['?'] * len(ids))})", ids)
        new_balance = balance - total
        c.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, uid))
        conn.commit()
        bought_names = "\n".join([i[1] for i in items])
        await ctx.send(
            f"``` Purchase Success!\n"
            f"--------------------------\n"
            f"Code   : {code}\n"
            f"Amount : {amount}\n"
            f"Price  : {price}\n"
            f"Total  : {total}\n"
            f"Balance: {new_balance}\n\n"
            f" Items:\n{bought_names}```"
        )
