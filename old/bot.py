import discord
from discord import app_commands
from discord.ext import commands
import random

TOKEN = "MTQ3MjA3ODAwMjE4MjQyNjcwNg.GDk9ak.-FZMiSkoR0_mXBBdgPy-h53qFFe44-d_LuxPoU"
GUILD_ID = 254277411313549313

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

fila = []

class FilaView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Entrar na Fila", style=discord.ButtonStyle.green)
    async def entrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        global fila

        if interaction.user in fila:
            await interaction.response.send_message("Você já está na fila.", ephemeral=True)
            return

        fila.append(interaction.user)
        await interaction.response.send_message("Você entrou na fila!", ephemeral=True)

        if len(fila) == 10:
            await criar_lobby(interaction.guild)

async def criar_lobby(guild):
    global fila

    random.shuffle(fila)
    time1 = fila[:5]
    time2 = fila[5:]

    categoria = await guild.create_category("LOBBY AUTOMÁTICA")

    canal_time1 = await guild.create_voice_channel("🔵 Time 1", category=categoria)
    canal_time2 = await guild.create_voice_channel("🔴 Time 2", category=categoria)

    for membro in time1:
        if membro.voice:
            await membro.move_to(canal_time1)

    for membro in time2:
        if membro.voice:
            await membro.move_to(canal_time2)

    canal_texto = await guild.create_text_channel("📢 lobby-info", category=categoria)

    await canal_texto.send(
        f"**Lobby Criada!**\n\n"
        f"🔵 Time 1:\n" + "\n".join([m.mention for m in time1]) +
        f"\n\n🔴 Time 2:\n" + "\n".join([m.mention for m in time2])
    )

    fila = []

@bot.tree.command(name="fila", description="Criar fila para lobby 5x5")
# @bot.tree.command(name="fila", guild=discord.Object(id=GUILD_ID))
async def fila_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Fila para Lobby 5x5",
        description="Clique no botão para entrar.\nQuando completar 10 jogadores, a lobby será criada automaticamente.",
        color=discord.Color.green()
    )

    await interaction.response.send_message(embed=embed, view=FilaView())

@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    await bot.tree.sync(guild=guild)
    print("Comandos sincronizados localmente.")

bot.run(TOKEN)
