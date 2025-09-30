from discord.ext import commands
from discord import app_commands
from utils import is_allowed_user, is_maintenance

def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register the deletproduct command."""
    # @bot.command(name="deletproduct", usage=f"{PREFIX}deletproduct <code>")
    @bot.hybrid_command(name="deleteproduct",
                        usage=f"{PREFIX}deleteproduct <cod>",
                        description="Delete product from database")
    @is_allowed_user()  # hanya user di ALLOWED_USERNAMES
    @is_maintenance()
    @app_commands.guilds(os.getenv("SERVER_ID"))
    async def deleteproduct(ctx, code: str):
        c.execute("SELECT judul, harga FROM stock WHERE kode = ?", (code,))
        row = c.fetchone()
        if not row:
            await ctx.send(f"❌ Code **{code}** not found.")
            return
        title, price = row[0], row[1]
        c.execute("SELECT COUNT(*) FROM stock_items WHERE kode = ?", (code,))
        item_count = c.fetchone()[0]
        c.execute("DELETE FROM stock_items WHERE kode = ?", (code,))
        c.execute("DELETE FROM stock WHERE kode = ?", (code,))
        conn.commit()
        # verify
        c.execute("SELECT 1 FROM stock WHERE kode = ?", (code,))
        still_exists = c.fetchone()
        status = "Failed" if still_exists else "Success"
        await ctx.send(
            f"```️ Product Deleted\n"
            f"--------------------------\n"
            f"Code   : {code}\n"
            f"Title  : {title}\n"
            f"Price  : {fmt_wl(price)} WL\n"
            f"Items  : {item_count} deleted\n"
            f"Status : {status}\n```"
        )
