import os
import discord
from discord import app_commands
from discord.ext import commands
from discord import ui
import asyncpg
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# Corrige Railway (postgres:// -> postgresql://)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

db_pool = None
fila = []


# =========================
# BANCO DE DADOS
# =========================

async def create_tables():
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                discord_id TEXT PRIMARY KEY,
                discord_name TEXT,
                dota_nick TEXT,
                medal TEXT,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0
            )
        """)


@bot.event
async def on_ready():
    global db_pool
    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        ssl="require"
    )
    await create_tables()
    await tree.sync()
    print(f"🔥 Bot online como {bot.user}")


# =========================
# MODAL DE CADASTRO
# =========================

class CadastroModal(ui.Modal, title="Cadastro DotaHub"):

    dota_nick = ui.TextInput(
        label="Seu nickname no Dota",
        placeholder="Ex: HC MONSTRO",
        required=True
    )

    medal = ui.TextInput(
        label="Sua medalha atual",
        placeholder="Ex: Ancient 3",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO players (discord_id, discord_name, dota_nick, medal)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (discord_id)
                DO UPDATE SET
                    discord_name = $2,
                    dota_nick = $3,
                    medal = $4
            """,
            str(interaction.user.id),
            interaction.user.name,
            self.dota_nick.value,
            self.medal.value
            )

        await interaction.response.send_message(
            "✅ Cadastro realizado com sucesso!",
            ephemeral=True
        )


# =========================
# VIEW PRINCIPAL
# =========================

class LobbyView(ui.View):

    @ui.button(label="Cadastrar", style=discord.ButtonStyle.primary)
    async def cadastrar(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(CadastroModal())

    @ui.button(label="Entrar na Fila", style=discord.ButtonStyle.success)
    async def entrar(self, interaction: discord.Interaction, button: ui.Button):

        async with db_pool.acquire() as conn:
            player = await conn.fetchrow(
                "SELECT * FROM players WHERE discord_id = $1",
                str(interaction.user.id)
            )

        if not player:
            await interaction.response.send_message(
                "⚠️ Você precisa se cadastrar primeiro.",
                ephemeral=True
            )
            return

        if interaction.user.id in fila:
            await interaction.response.send_message(
                "Você já está na fila.",
                ephemeral=True
            )
            return

        fila.append(interaction.user.id)

        await interaction.response.send_message(
            f"🎮 {interaction.user.mention} entrou na fila!\n"
            f"Jogadores na fila: {len(fila)}/10"
        )

        if len(fila) == 10:
            await interaction.channel.send("🔥 10 jogadores! Lobby pode ser criado!")
            fila.clear()


# =========================
# COMANDOS
# =========================

@tree.command(name="painel", description="Abrir painel do DotaHub")
async def painel(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🎮 Painel DotaHub",
        view=LobbyView()
    )


@tree.command(name="perfil", description="Ver seu perfil")
async def perfil(interaction: discord.Interaction):

    async with db_pool.acquire() as conn:
        player = await conn.fetchrow(
            "SELECT * FROM players WHERE discord_id = $1",
            str(interaction.user.id)
        )

    if not player:
        await interaction.response.send_message(
            "Você não está cadastrado.",
            ephemeral=True
        )
        return

    embed = discord.Embed(title="📊 Seu Perfil", color=0x00ff00)
    embed.add_field(name="Nick Dota", value=player["dota_nick"], inline=False)
    embed.add_field(name="Medalha", value=player["medal"], inline=False)
    embed.add_field(name="Wins", value=player["wins"])
    embed.add_field(name="Losses", value=player["losses"])

    await interaction.response.send_message(embed=embed)


@tree.command(name="ranking", description="Ver ranking")
async def ranking(interaction: discord.Interaction):

    async with db_pool.acquire() as conn:
        players = await conn.fetch("""
            SELECT * FROM players
            ORDER BY wins DESC
            LIMIT 10
        """)

    if not players:
        await interaction.response.send_message("Nenhum jogador cadastrado.")
        return

    embed = discord.Embed(title="🏆 Ranking DotaHub", color=0xffd700)

    for i, player in enumerate(players, start=1):
        embed.add_field(
            name=f"{i}º - {player['dota_nick']}",
            value=f"Wins: {player['wins']} | Losses: {player['losses']}",
            inline=False
        )

    await interaction.response.send_message(embed=embed)


# =========================

bot.run(TOKEN)
