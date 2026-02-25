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

MEDAL_MMR = {
    "herald": 500,
    "guardian": 1000,
    "crusader": 1500,
    "archon": 2000,
    "legend": 2500,
    "ancient": 3000,
    "divine": 4000,
    "immortal": 5000
}


class DotaHubBot(commands.Bot):
    async def setup_hook(self):
        global pool
        pool = await asyncpg.create_pool(DATABASE_URL)

        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    user_id BIGINT PRIMARY KEY,
                    dota_nick TEXT,
                    medal TEXT,
                    mmr INTEGER
                );
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS queue (
                    user_id BIGINT PRIMARY KEY,
                    joined_at TIMESTAMP DEFAULT NOW()
                );
            """)

        guild = discord.Object(id=GUILD_ID)
        await self.tree.sync(guild=guild)
        print("Bot pronto.")

bot = DotaHubBot(command_prefix="!", intents=intents)


# =========================
# MODAL
# =========================
class CadastroModal(discord.ui.Modal, title="Cadastro DotaHub"):

    dota_nick = discord.ui.TextInput(label="Seu nickname no Dota", required=True)
    medal = discord.ui.TextInput(label="Sua medalha (Ex: Ancient 3)", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        medal_text = self.medal.value.lower().split()[0]
        mmr = MEDAL_MMR.get(medal_text, 1000)

        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO players (user_id, dota_nick, medal, mmr)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id) DO NOTHING;
            """,
                interaction.user.id,
                self.dota_nick.value,
                self.medal.value,
                mmr
            )

        await interaction.response.send_message(
            "Cadastro feito. Clique novamente para entrar na fila.",
            ephemeral=True
        )


# =========================
# VIEW
# =========================
class FilaView(discord.ui.View):

    @discord.ui.button(label="Entrar na Fila", style=discord.ButtonStyle.green)
    async def entrar(self, interaction: discord.Interaction, button: discord.ui.Button):

        async with pool.acquire() as conn:
            player = await conn.fetchrow(
                "SELECT * FROM players WHERE user_id = $1",
                interaction.user.id
            )

            if not player:
                await interaction.response.send_modal(CadastroModal())
                return

            count = await conn.fetchval("SELECT COUNT(*) FROM queue")

            if count >= 10:
                await interaction.response.send_message(
                    "Lobby já está cheio (10 jogadores).",
                    ephemeral=True
                )
                return

            await conn.execute("""
                INSERT INTO queue (user_id)
                VALUES ($1)
                ON CONFLICT DO NOTHING;
            """, interaction.user.id)

        await interaction.response.send_message(
            "Você entrou na fila.",
            ephemeral=True
        )


# =========================
# SLASH
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


bot.run(TOKEN)
