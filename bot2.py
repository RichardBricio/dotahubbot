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
# MMR BASE POR MEDALHA
# =========================
MEDAL_MMR = {
    "herald": 500,
    "guardian": 770,
    "crusader": 1540,
    "archon": 2310,
    "legend": 3080,
    "ancient": 3850,
    "divine": 4620,
    "immortal": 5630
}

# Emoji para ilustrar
MEDAL_EMOJI = {
    "herald": "🟤",
    "guardian": "🟢",
    "crusader": "🔵",
    "archon": "🟣",
    "legend": "🟡",
    "ancient": "🔶",
    "divine": "💎",
    "immortal": "🔥"
}

# =========================
# BOT CLASS
# =========================
class DotaHubBot(commands.Bot):

    async def setup_hook(self):
        global pool
        pool = await asyncpg.create_pool(DATABASE_URL)

        await create_tables()

        guild = discord.Object(id=GUILD_ID)

        synced = await self.tree.sync(guild=guild)
        print(f"{len(synced)} comandos sincronizados.")

bot = DotaHubBot(command_prefix="!", intents=intents)

# =========================
# DATABASE
# =========================
async def create_tables():
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                user_id BIGINT PRIMARY KEY,
                discord_name TEXT,
                dota_nick TEXT,
                medal TEXT,
                mmr INTEGER,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                points INTEGER DEFAULT 0
            );
        """)

# =========================
# MODAL CADASTRO
# =========================
class CadastroModal(discord.ui.Modal, title="Cadastro DotaHub"):

    dota_nick = discord.ui.TextInput(
        label="Seu Nick no Dota",
        required=True,
        max_length=30
    )

    medal = discord.ui.TextInput(
        label="Sua Medalha",
        placeholder="Herald, Guardian, Crusader...",
        required=True,
        max_length=20
    )

    async def on_submit(self, interaction: discord.Interaction):

        medal_input = self.medal.value.lower().strip()

        if medal_input not in MEDAL_MMR:
            await interaction.response.send_message(
                "Medalha inválida. Use: Herald, Guardian, Crusader, Archon, Legend, Ancient, Divine ou Immortal.",
                ephemeral=True
            )
            return

        mmr = MEDAL_MMR[medal_input]
        emoji = MEDAL_EMOJI[medal_input]

        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO players (user_id, discord_name, dota_nick, medal, mmr, points)
                VALUES ($1, $2, $3, $4, $5, 0)
                ON CONFLICT (user_id)
                DO UPDATE SET
                    discord_name = EXCLUDED.discord_name,
                    dota_nick = EXCLUDED.dota_nick,
                    medal = EXCLUDED.medal,
                    mmr = EXCLUDED.mmr;
            """,
                interaction.user.id,
                interaction.user.display_name,
                self.dota_nick.value,
                medal_input.capitalize(),
                mmr
            )

        await interaction.response.send_message(
            f"Cadastro concluído {emoji}\nMedalha: {medal_input.capitalize()} ({mmr} MMR)",
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
# COMANDOS
# =========================
@bot.tree.command(name="fila", description="Abrir painel da fila")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def fila(interaction: discord.Interaction):

    embed = discord.Embed(
        title="DotaHub Ranked Queue",
        description="Clique para entrar na fila.",
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
        await interaction.response.send_message("Nenhum jogador cadastrado.")
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
