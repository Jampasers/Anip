from discord import app_commands
import os

DEFAULT_RATE_100_WL_RUPIAH = int(os.getenv("RATE_100_WL_RUPIAH", "210"))
RATE_SETTINGS_ID = 1
ALLOWED_ADMIN_IDS = {x.strip() for x in os.getenv("ALLOWED_ADMIN_IDS", "").split(",") if x.strip()}


def ensure_qris_rate_schema(cur, connection):
    """Create qris_rate_settings table and seed default rate."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS qris_rate_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            rate_100_wl INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        "INSERT OR IGNORE INTO qris_rate_settings (id, rate_100_wl) VALUES (?, ?)",
        (RATE_SETTINGS_ID, max(1, DEFAULT_RATE_100_WL_RUPIAH)),
    )
    connection.commit()


def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register /rate command for updating QRIS WL conversion rate."""
    ensure_qris_rate_schema(c, conn)

    @bot.hybrid_command(
        name="rate",
        usage=f"{PREFIX}rate <rupiah>",
        description="Update rate QRIS: 100 WL = Rp <rupiah>",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(rupiah="Harga 100 WL dalam rupiah. Contoh: 290")
    async def rate(ctx, rupiah: int):
        has_admin_perm = bool(
            getattr(ctx.author, "guild_permissions", None)
            and ctx.author.guild_permissions.administrator
        )
        is_allowed_id = str(ctx.author.id) in ALLOWED_ADMIN_IDS
        if not (has_admin_perm or is_allowed_id):
            await ctx.send("Command ini khusus admin.")
            return

        if rupiah <= 0:
            await ctx.send("Nilai rate harus lebih besar dari 0.")
            return

        c.execute("SELECT rate_100_wl FROM qris_rate_settings WHERE id = ?", (RATE_SETTINGS_ID,))
        row = c.fetchone()
        old_rate = int(row[0]) if row and row[0] is not None else max(1, DEFAULT_RATE_100_WL_RUPIAH)

        c.execute(
            "INSERT OR REPLACE INTO qris_rate_settings (id, rate_100_wl) VALUES (?, ?)",
            (RATE_SETTINGS_ID, int(rupiah)),
        )
        conn.commit()

        wl_per_1000 = int((1000 * 100) / int(rupiah))
        await ctx.send(
            f"```Rate QRIS Updated\n"
            f"--------------------------\n"
            f"Old : 100 WL = Rp {fmt_wl(old_rate)}\n"
            f"New : 100 WL = Rp {fmt_wl(rupiah)}\n"
            f"Rp 1.000 ~= {fmt_wl(wl_per_1000)} WL```"
        )
