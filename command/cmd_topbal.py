from discord.ext import commands
from discord import ButtonStyle, Embed, Interaction
from discord.ui import Button, View
import math
from utils import is_allowed_user, is_maintenance
from discord import app_commands
import os

def setup(bot, c, conn, fmt_wl, PREFIX):
    """Register the topbal command with a paginated leaderboard."""
    class TopBalanceView(View):
        def __init__(self, data, fmt_wl, page=0):
            super().__init__(timeout=60)
            self.data = data
            self.page = page
            self.fmt_wl = fmt_wl
            self.total_pages = math.ceil(len(data) / 10)
            self.update_buttons()

        def get_embed(self):
            start = self.page * 10
            end = start + 10
            sliced = self.data[start:end]
            emoji = ["", "", "", "", "️"]
            msg = ""
            for i, (nama, balance) in enumerate(sliced, start=1 + start):
                icon = emoji[i - 1] if i <= 5 else f"{i}."
                msg += f"{icon} {nama} - {self.fmt_wl(balance)} WL\n"
            embed = Embed(title=" Top Balance Leaderboard", description=msg, color=0xFFD700)
            embed.set_footer(text=f"Page {self.page + 1} of {self.total_pages}")
            return embed

        def update_buttons(self):
            self.clear_items()
            prev_button = Button(label="⬅️ Prev", style=ButtonStyle.primary, disabled=self.page == 0)
            next_button = Button(label="➡️ Next", style=ButtonStyle.primary, disabled=self.page >= self.total_pages - 1)
            async def prev_callback(interaction: Interaction):
                self.page -= 1
                self.update_buttons()
                await interaction.response.edit_message(embed=self.get_embed(), view=self)
            async def next_callback(interaction: Interaction):
                self.page += 1
                self.update_buttons()
                await interaction.response.edit_message(embed=self.get_embed(), view=self)
            prev_button.callback = prev_callback
            next_button.callback = next_callback
            self.add_item(prev_button)
            self.add_item(next_button)

    # @bot.command(name="topbal", usage=f"{PREFIX}topbal")
    @bot.hybrid_command(name="topbal",
                        usage=f"{PREFIX}topbal",
                        description="Top balance di bot")
    @is_maintenance()
    @app_commands.guilds(os.getenv("SERVER_ID"))
    async def topbal(ctx):
        c.execute("SELECT nama, balance FROM users WHERE balance > 0 ORDER BY balance DESC")
        rows = c.fetchall()
        if not rows:
            await ctx.send("❌ Tidak ada data balance.")
            return
        view = TopBalanceView(rows, fmt_wl)
        await ctx.send(embed=view.get_embed(), view=view)
