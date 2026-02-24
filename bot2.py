import os
import discord
from discord.ext import commands
from discord import app_commands

TOKEN = os.getenv("TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))

intents = discord.Intents.default()

class DotaHubBot(commands.Bot):
    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        await self.tree.sync(guild=guild)
        print("Sync feito.")

bot = DotaHubBot(command_prefix="!", intents=intents)


# =========================
# MODAL
# =========================
class CadastroModal(discord.ui.Modal, title="Cadastro DotaHub"):

    dota_nick = discord.ui.TextInput(
        label="Seu nickname no Dota",
        required=True
    )

    medal = discord.ui.TextInput(
        label="Sua medalha",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Nick: {self.dota_nick.value}\nMedalha: {self.medal.value}",
            ephemeral=True
        )


# =========================
# VIEW
# =========================
class FilaView(discord.ui.View):

    @discord.ui.button(label="Entrar na Fila", style=discord.ButtonStyle.green)
    async def entrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CadastroModal())


# =========================
# SLASH
# =========================
@bot.tree.command(name="fila", description="Abrir painel da fila")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def fila(interaction: discord.Interaction):

    embed = discord.Embed(
        title="DotaHub Ranked Queue",
        description="Clique no botão abaixo.",
        color=discord.Color.red()
    )

    await interaction.response.send_message(embed=embed, view=FilaView())


bot.run(TOKEN)
