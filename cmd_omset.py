import discord
from discord.ext import commands, tasks
from utils import is_allowed_user, is_maintenance
from datetime import datetime

PERIODS = ("today", "week", "month", "total")

def setup(bot, c, conn, fmt_wl, PREFIX):
    state = {
        "channel_id": None,
        "message_id": None,
        "period": "today",
    }

    # ---------------------------
    # DATA LAYER
    # ---------------------------
    def q_sum(period: str) -> int:
        if period == "today":
            c.execute("""
                SELECT COALESCE(SUM(t.jumlah * s.harga), 0)
                FROM transactions t
                JOIN stock s ON t.kode = s.kode
                WHERE DATE(t.waktu) = DATE('now','localtime')
            """)
        elif period == "week":
            c.execute("""
                SELECT COALESCE(SUM(t.jumlah * s.harga), 0)
                FROM transactions t
                JOIN stock s ON t.kode = s.kode
                WHERE strftime('%W', t.waktu)=strftime('%W','now','localtime')
                  AND strftime('%Y', t.waktu)=strftime('%Y','now','localtime')
            """)
        elif period == "month":
            c.execute("""
                SELECT COALESCE(SUM(t.jumlah * s.harga), 0)
                FROM transactions t
                JOIN stock s ON t.kode = s.kode
                WHERE strftime('%m', t.waktu)=strftime('%m','now','localtime')
                  AND strftime('%Y', t.waktu)=strftime('%Y','now','localtime')
            """)
        else:
            c.execute("""
                SELECT COALESCE(SUM(t.jumlah * s.harga), 0)
                FROM transactions t
                JOIN stock s ON t.kode = s.kode
            """)
        return int(c.fetchone()[0] or 0)

    def q_top_products(period: str, limit=5):
        if period == "today":
            where = "DATE(t.waktu) = DATE('now','localtime')"
        elif period == "week":
            where = "strftime('%W', t.waktu)=strftime('%W','now','localtime') AND strftime('%Y', t.waktu)=strftime('%Y','now','localtime')"
        elif period == "month":
            where = "strftime('%m', t.waktu)=strftime('%m','now','localtime') AND strftime('%Y', t.waktu)=strftime('%Y','now','localtime')"
        else:
            where = "1=1"

        c.execute(f"""
            SELECT t.kode, SUM(t.jumlah) as qty
            FROM transactions t
            WHERE {where}
            GROUP BY t.kode
            ORDER BY qty DESC
            LIMIT ?
        """, (limit,))
        return c.fetchall()

    def q_prev_sum(period: str) -> int:
        """Buat panah tren (perbandingan periode sebelumnya)."""
        if period == "today":
            c.execute("""
                SELECT COALESCE(SUM(t.jumlah * s.harga), 0)
                FROM transactions t
                JOIN stock s ON t.kode = s.kode
                WHERE DATE(t.waktu) = DATE('now','localtime','-1 day')
            """)
        elif period == "week":
            c.execute("""
                SELECT COALESCE(SUM(t.jumlah * s.harga), 0)
                FROM transactions t
                JOIN stock s ON t.kode = s.kode
                WHERE strftime('%W', t.waktu)=strftime('%W','now','localtime','-7 days')
                  AND strftime('%Y', t.waktu)=strftime('%Y','now','localtime','-7 days')
            """)
        elif period == "month":
            c.execute("""
                SELECT COALESCE(SUM(t.jumlah * s.harga), 0)
                FROM transactions t
                JOIN stock s ON t.kode = s.kode
                WHERE strftime('%m', t.waktu)=strftime('%m','now','localtime','-1 month')
                  AND strftime('%Y', t.waktu)=strftime('%Y','now','localtime','-1 month')
            """)
        else:
            return 0
        return int(c.fetchone()[0] or 0)

    # ---------------------------
    # PRESENTATION
    # ---------------------------
    def period_label(p: str) -> str:
        return {
            "today": "Hari Ini",
            "week": "Minggu Ini",
            "month": "Bulan Ini",
            "total": "Total",
        }[p]

    def trend_arrow(curr: int, prev: int) -> str:
        if prev <= 0 and curr > 0:
            return "📈 +∞"
        if curr == prev:
            return "⏸️ 0%"
        diff = curr - prev
        pct = (diff / prev * 100) if prev else 100.0
        return ("📈 +" if diff > 0 else "📉 ") + f"{abs(pct):.1f}%"

    def rank_emoji(i: int) -> str:
        return ["🥇","🥈","🥉","④","⑤"][i] if i < 5 else "•"

    def bars(value: int, top_list: list) -> str:
        # mini bar untuk tiap produk biar profesional tapi ringan
        total_qty = max([q for _, q in top_list] + [1])
        filled = max(1, int((value/total_qty) * 10))
        return "▮" * filled + "▯" * (10 - filled)

    def build_embed(period: str) -> discord.Embed:
        now = datetime.now()
        total_now = q_sum(period)
        total_prev = q_prev_sum(period)
        top = q_top_products(period)

        # header & warna korporat: biru tua
        embed = discord.Embed(
            title="💼 Store Analytics",
            description=f"**{period_label(period)}** • ringkasan penjualan",
            color=discord.Color.dark_blue()
        )

        # angka utama + tren
        arrow = trend_arrow(total_now, total_prev) if period != "total" else "—"
        embed.add_field(
            name="💰 Omzet",
            value=f"**`{fmt_wl(total_now)} WL`**\n{arrow}",
            inline=True
        )

        # item terlaris
        if top:
            lines = []
            for i, (kode, qty) in enumerate(top):
                lines.append(
                    f"{rank_emoji(i)} **{kode.upper()}**  `x{qty}`  {bars(qty, top)}"
                )
            products_block = "\n".join(lines)
        else:
            products_block = "_Belum ada transaksi._"

        embed.add_field(
            name="🏆 Produk Terlaris",
            value=products_block,
            inline=False
        )

        # footer
        embed.set_footer(text=f"🔔 Update: {now.strftime('%d %b %Y • %H:%M:%S')}  |  Mode: {period_label(period)}")
        return embed

    # ---------------------------
    # VIEW (INTERAKTIF EMOJI)
    # ---------------------------
    class OmsetView(discord.ui.View):
        def __init__(self, timeout=1800):
            super().__init__(timeout=timeout)
            self.update_labels()

        def update_labels(self):
            # Highlight tombol periode aktif (pakai gaya sekunder/primer)
            for child in self.children:
                if isinstance(child, discord.ui.Button) and child.custom_id in PERIODS:
                    child.style = discord.ButtonStyle.primary if child.custom_id == state["period"] else discord.ButtonStyle.secondary

        async def refresh(self, interaction: discord.Interaction):
            self.update_labels()
            try:
                await interaction.response.edit_message(embed=build_embed(state["period"]), view=self)
            except discord.InteractionResponded:
                await interaction.edit_original_response(embed=build_embed(state["period"]), view=self)

        @discord.ui.button(emoji="📅", label="Hari Ini", style=discord.ButtonStyle.secondary, custom_id="today")
        async def btn_today(self, interaction: discord.Interaction, button: discord.ui.Button):
            state["period"] = "today"
            await self.refresh(interaction)

        @discord.ui.button(emoji="📈", label="Minggu", style=discord.ButtonStyle.secondary, custom_id="week")
        async def btn_week(self, interaction: discord.Interaction, button: discord.ui.Button):
            state["period"] = "week"
            await self.refresh(interaction)

        @discord.ui.button(emoji="🗓️", label="Bulan", style=discord.ButtonStyle.secondary, custom_id="month")
        async def btn_month(self, interaction: discord.Interaction, button: discord.ui.Button):
            state["period"] = "month"
            await self.refresh(interaction)

        @discord.ui.button(emoji="🏦", label="Total", style=discord.ButtonStyle.secondary, custom_id="total")
        async def btn_total(self, interaction: discord.Interaction, button: discord.ui.Button):
            state["period"] = "total"
            await self.refresh(interaction)

        @discord.ui.button(emoji="🔄", label="Refresh", style=discord.ButtonStyle.success, custom_id="refresh")
        async def btn_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.refresh(interaction)

        @discord.ui.button(emoji="📤", label="Export", style=discord.ButtonStyle.secondary, custom_id="export")
        async def btn_export(self, interaction: discord.Interaction, button: discord.ui.Button):
            # placeholder export: tinggal isi sesuai fungsi export kamu
            await interaction.response.send_message(
                content="📤 Export laporan di-generate… (fitur export bisa diarahkan ke Excel/PDF)",
                ephemeral=True
            )

    # ---------------------------
    # COMMAND
    # ---------------------------
    @bot.command(name="omset", usage=f"{PREFIX}omset")
    @is_allowed_user()
    @is_maintenance()
    async def omset(ctx: commands.Context):
        """Tampilkan panel analitik dengan UI interaktif ber-emoji."""
        state["channel_id"] = ctx.channel.id
        view = OmsetView()
        msg = await ctx.send(embed=build_embed(state["period"]), view=view)
        state["message_id"] = msg.id
        if not _auto_refresh.is_running():
            _auto_refresh.start()

    # ---------------------------
    # AUTO REFRESH LOOP (10s)
    # ---------------------------
    @tasks.loop(seconds=10)
    async def _auto_refresh():
        if not state["channel_id"] or not state["message_id"]:
            return
        ch = bot.get_channel(state["channel_id"])
        if not ch:
            return
        try:
            msg = await ch.fetch_message(state["message_id"])
        except Exception:
            return
        view = OmsetView()
        await msg.edit(embed=build_embed(state["period"]), view=view)

    @_auto_refresh.before_loop
    async def _before():
        await bot.wait_until_ready()
