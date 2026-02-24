import discord
from discord.ext import commands
from discord import app_commands
from config import TOKEN, GUILD_ID
from views import QueueView
from medals import get_medal
import database

database.setup()

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

database.setup()

@bot.event
async def on_ready():
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"DotaHub online como {bot.user}")

@bot.tree.command(name="fila", description="Entrar na fila ranqueada")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def fila(interaction: discord.Interaction):
    embed = discord.Embed(
        title="DotaHub Ranked Queue",
        description="Clique no botão para entrar na fila.",
        color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed, view=QueueView())

@bot.tree.command(name="ranking", description="Ver ranking")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def ranking(interaction: discord.Interaction):
    top = database.top10()
    msg = ""
    for i, p in enumerate(top):
        user = await bot.fetch_user(p[0])
        msg += f"{i+1}. {user.name} - {p[1]} MMR ({p[2]}W/{p[3]}L)\n"
    await interaction.response.send_message(msg)

@bot.tree.command(name="perfil", description="Ver seu perfil")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def perfil(interaction: discord.Interaction):
    player = database.get_player(interaction.user.id)
    if not player:
        await interaction.response.send_message("Você ainda não jogou.")
        return

    medal = get_medal(player[0])
    winrate = round(player[1] / (player[1] + player[2]) * 100, 1) if (player[1]+player[2])>0 else 0

    embed = discord.Embed(title=f"Perfil de {interaction.user.name}")
    embed.add_field(name="MMR", value=player[0])
    embed.add_field(name="Medalha", value=medal)
    embed.add_field(name="Winrate", value=f"{winrate}%")
    await interaction.response.send_message(embed=embed)

bot.run(TOKEN)

