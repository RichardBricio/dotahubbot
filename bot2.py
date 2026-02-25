import os
import discord
from discord.ext import commands
from discord import app_commands
import asyncpg

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GUILD_ID = int(os.getenv("GUILD_ID"))

print("TOKEN:", TOKEN)
print("DATABASE_URL:", DATABASE_URL)
print("GUILD_ID:", GUILD_ID)

intents = discord.Intents.default()
pool = None

# =========================
# MMR BASE POR MEDALHA
# =========================
MEDAL_MMR = {
    "Herald": 500,
    "Guardian": 770,
    "Crusader": 1540,
    "Archon": 2310,
    "Legend": 3080,
    "Ancient": 3850,
    "Divine": 4620,
    "Immortal": 5630
}

# =========================
# BOT CLASS
# =========================
class DotaHubBot(commands.Bot):

    async def setup_hook(self):
        global pool
    
        pool = await asyncpg.create_pool(DATABASE_URL)
        await create_tables()
    
        # Sync GLOBAL
        await self.tree.sync()
    
        print("Slash commands sincronizados globalmente.")

bot = DotaHubBot(command_prefix="!", intents=intents)

# =========================
# DATABASE SETUP
# =========================
async def create_tables():
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                user_id BIGINT PRIMARY KEY,
                discord_name TEXT,
                dota_nick TEXT,
                medal TEXT,
                mmr INTEGER DEFAULT 1000,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                points INTEGER DEFAULT 0
            );
        """)

# =========================
# MODAL CADASTRO
# =========================
class CadastroModal(discord.ui.Modal, title="Cadastro DotaHub"):

    def __init__(self):
        super().__init__()

        self.dota_nick = discord.ui.TextInput(
            label="Seu nickname no Dota",
            placeholder="Ex: HC GOD",
            required=True
        )

        self.add_item(self.dota_nick)

    async def on_submit(self, interaction: discord.Interaction):
        # abre dropdown de medalha após nickname
        await interaction.response.send_message(
            "Selecione sua medalha:",
            view=MedalSelectView(self.dota_nick.value),
            ephemeral=True
        )

# =========================
# SELECT MEDALHA
# =========================
class MedalSelect(discord.ui.Select):

    def __init__(self, dota_nick):

        self.dota_nick = dota_nick

        options = [
            discord.SelectOption(label="Herald"),
            discord.SelectOption(label="Guardian"),
            discord.SelectOption(label="Crusader"),
            discord.SelectOption(label="Archon"),
            discord.SelectOption(label="Legend"),
            discord.SelectOption(label="Ancient"),
            discord.SelectOption(label="Divine"),
            discord.SelectOption(label="Immortal"),
        ]

        super().__init__(
            placeholder="Escolha sua medalha",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):

        medal = self.values[0]
        mmr = MEDAL_MMR[medal]

        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO players (user_id, discord_name, dota_nick, medal, mmr)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id)
                DO UPDATE SET
                    discord_name = EXCLUDED.discord_name;
            """,
                interaction.user.id,
                interaction.user.display_name,
                self.dota_nick,
                medal,
                mmr
            )

        await interaction.response.send_message(
            f"Cadastro concluído como **{medal}** ({mmr} MMR).",
            ephemeral=True
        )

class MedalSelectView(discord.ui.View):
    def __init__(self, dota_nick):
        super().__init__(timeout=60)
        self.add_item(MedalSelect(dota_nick))

# =========================
# FILA VIEW
# =========================
class FilaView(discord.ui.View):

    @discord.ui.button(label="Entrar na Fila", style=discord.ButtonStyle.green)
    async def entrar(self, interaction: discord.Interaction, button: discord.ui.Button):

        async with pool.acquire() as conn:
            player = await conn.fetchrow(
                "SELECT * FROM players WHERE user_id = $1",
                interaction.user.id
            )

        if player is None:
            await interaction.response.send_modal(CadastroModal())
            return

        await interaction.response.send_message(
            "Você entrou na fila!",
            ephemeral=True
        )

# =========================
# SLASH COMMANDS
# =========================
@bot.tree.command(name="fila", description="Abrir painel da fila")
#@app_commands.guilds(discord.Object(id=GUILD_ID))
async def fila(interaction: discord.Interaction):

    embed = discord.Embed(
        title="DotaHub Ranked Queue",
        description="Clique no botão abaixo para entrar na fila.",
        color=discord.Color.red()
    )

    await interaction.response.send_message(embed=embed, view=FilaView())


@bot.tree.command(name="ranking", description="Ver ranking geral")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def ranking(interaction: discord.Interaction):

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, mmr, wins, losses, points
            FROM players
            ORDER BY points DESC
            LIMIT 10
        """)

    if not rows:
        await interaction.response.send_message("Nenhum jogador cadastrado ainda.")
        return

    msg = ""

    for i, row in enumerate(rows):
        user = await bot.fetch_user(row["user_id"])
        msg += f"{i+1}. {user.name} - {row['points']} pts | {row['mmr']} MMR ({row['wins']}W/{row['losses']}L)\n"

    await interaction.response.send_message(msg)


@bot.tree.command(name="perfil", description="Ver seu perfil")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def perfil(interaction: discord.Interaction):

    async with pool.acquire() as conn:
        player = await conn.fetchrow(
            "SELECT * FROM players WHERE user_id = $1",
            interaction.user.id
        )

    if not player:
        await interaction.response.send_message("Você ainda não está cadastrado.")
        return

    total_games = player["wins"] + player["losses"]
    winrate = round((player["wins"] / total_games) * 100, 1) if total_games > 0 else 0

    embed = discord.Embed(
        title=f"Perfil de {interaction.user.display_name}",
        color=discord.Color.gold()
    )

    embed.add_field(name="Dota Nick", value=player["dota_nick"], inline=False)
    embed.add_field(name="Medalha", value=player["medal"], inline=False)
    embed.add_field(name="MMR", value=player["mmr"])
    embed.add_field(name="Vitórias", value=player["wins"])
    embed.add_field(name="Derrotas", value=player["losses"])
    embed.add_field(name="Pontos", value=player["points"])
    embed.add_field(name="Winrate", value=f"{winrate}%")

    await interaction.response.send_message(embed=embed)

# =========================
# RUN
# =========================
bot.run(TOKEN)

