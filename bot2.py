import os
import discord
from discord.ext import commands
from discord import app_commands
import asyncpg

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GUILD_ID = int(os.getenv("GUILD_ID"))

intents = discord.Intents.default()
pool = None


# =========================
# BOT CLASS
# =========================
class DotaHubBot(commands.Bot):

    async def setup_hook(self):
        global pool
    
        pool = await asyncpg.create_pool(
            DATABASE_URL,
            ssl="require"
        )
    
        await create_tables()
    
        guild = discord.Object(id=GUILD_ID)
        self.tree.clear_commands(guild=guild)
        await self.tree.sync(guild=guild)
    
        print("Banco conectado e slash sincronizado.")

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
                losses INTEGER DEFAULT 0
            );
        """)


# =========================
# CADASTRO MODAL
# =========================
class CadastroModal(discord.ui.Modal, title="Cadastro DotaHub"):

    dota_nick = discord.ui.TextInput(
        label="Seu nickname no Dota",
        placeholder="Ex: MAMACO HC GOD",
        required=True
    )

    medal = discord.ui.TextInput(
        label="Sua medalha atual",
        placeholder="Ex: Ancient 3",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO players (user_id, discord_name, dota_nick, medal)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id) DO NOTHING;
            """,
                interaction.user.id,
                interaction.user.name,
                self.dota_nick.value,
                self.medal.value
            )

        await interaction.response.send_message(
            "Cadastro realizado com sucesso. Agora clique novamente para entrar na fila.",
            ephemeral=True
        )


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
@app_commands.guilds(discord.Object(id=GUILD_ID))
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
            SELECT user_id, mmr, wins, losses
            FROM players
            ORDER BY mmr DESC
            LIMIT 10
        """)

    if not rows:
        await interaction.response.send_message("Nenhum jogador cadastrado ainda.")
        return

    msg = ""
    for i, row in enumerate(rows):
        user = await bot.fetch_user(row["user_id"])
        msg += f"{i+1}. {user.name} - {row['mmr']} MMR ({row['wins']}W/{row['losses']}L)\n"

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
        title=f"Perfil de {interaction.user.name}",
        color=discord.Color.gold()
    )

    embed.add_field(name="Dota Nick", value=player["dota_nick"], inline=False)
    embed.add_field(name="Medalha", value=player["medal"], inline=False)
    embed.add_field(name="MMR", value=player["mmr"])
    embed.add_field(name="Vitórias", value=player["wins"])
    embed.add_field(name="Derrotas", value=player["losses"])
    embed.add_field(name="Winrate", value=f"{winrate}%")

    await interaction.response.send_message(embed=embed)


bot.run(TOKEN)

