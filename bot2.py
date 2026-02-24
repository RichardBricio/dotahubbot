import os
import discord
from discord.ext import commands
from discord import app_commands

TOKEN = os.getenv("TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))

intents = discord.Intents.default()

class TestBot(commands.Bot):
    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        await self.tree.sync(guild=guild)
        print("Sync feito.")

bot = TestBot(command_prefix="!", intents=intents)

@bot.tree.command(name="fila", description="Teste fila")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def fila(interaction: discord.Interaction):
    await interaction.response.send_message("Funcionando.")

bot.run(TOKEN)
