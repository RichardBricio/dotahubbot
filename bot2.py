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
queue_message = None
queue_task = None
queue_started_at = None

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

QUEUE_SIZE = 2  
#QUEUE_SIZE = 10  
QUEUE_TIMEOUT = 300  # 5 minutos em segundos

# =========================
# BOT CLASS
# =========================
class DotaHubBot(commands.Bot):

    async def setup_hook(self):
        global pool
        pool = await asyncpg.create_pool(DATABASE_URL)

        await create_tables()

        guild = discord.Object(id=GUILD_ID)

        # limpa tudo
        #self.tree.clear_commands(guild=None)
        #self.tree.clear_commands(guild=guild)

        # força sincronização limpa
        await self.tree.sync()
        await self.tree.sync(guild=guild)

        print("Comandos resetados e sincronizados 100%.")

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

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                user_id BIGINT PRIMARY KEY,
                joined_at TIMESTAMP DEFAULT NOW()
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
            discord.SelectOption(label="Herald", emoji="<:herald:1476240174634631418>"),
            discord.SelectOption(label="Guardian", emoji="<:guardian:1476240153243553966>"),
            discord.SelectOption(label="Crusader", emoji="<:crusader:1476239755015618560>"),
            discord.SelectOption(label="Archon", emoji="<:archon:1476239723029725276>"),
            discord.SelectOption(label="Legend", emoji="<:legend:1476240219853553745>"),
            discord.SelectOption(label="Ancient", emoji="<:ancient:1476239613117862064>"),
            discord.SelectOption(label="Divine", emoji="<:divine:1476240241936568561>"),
            discord.SelectOption(label="Immortal", emoji="<:immortal:1476240204842143787>"),
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
                INSERT INTO players (user_id, discord_name, dota_nick, medal, mmr, points)
                VALUES ($1, $2, $3, $4, $5, 0)
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
        
        await interaction.followup.send(
            "Você foi automaticamente incluído na fila.",
            ephemeral=True
        )

# força clique automático
view = FilaView()
await view.entrar(interaction, None)

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
    
        global queue_message, queue_task, queue_started_at
    
        async with pool.acquire() as conn:
    
            # verifica cadastro
            player = await conn.fetchrow(
                "SELECT * FROM players WHERE user_id = $1",
                interaction.user.id
            )
    
            if player is None:
                await interaction.response.send_modal(CadastroModal())
                return
    
            # tenta inserir
            try:
                await conn.execute("""
                    INSERT INTO queue (user_id)
                    VALUES ($1)
                """, interaction.user.id)
            except asyncpg.UniqueViolationError:
                await interaction.response.send_message(
                    "Você já está na fila.",
                    ephemeral=True
                )
                return
    
            count = await conn.fetchval("SELECT COUNT(*) FROM queue")
    
        # inicia fila se for o primeiro
        if count == 1:
            queue_started_at = discord.utils.utcnow()
            queue_task = bot.loop.create_task(queue_timeout_task(interaction.channel))
    
        remaining = QUEUE_SIZE - count
        elapsed = (discord.utils.utcnow() - queue_started_at).total_seconds()
        time_left = max(0, QUEUE_TIMEOUT - int(elapsed))
    
        minutes = time_left // 60
        seconds = time_left % 60
    
        content = f"🔥 Fila rolando: {count}/{QUEUE_SIZE}\n⏳ Tempo restante: {minutes}:{seconds:02d}"
    
        if queue_message is None:
            queue_message = await interaction.channel.send(content)
        else:
            await queue_message.edit(content=content)
    
        await interaction.response.send_message("Você entrou na fila.", ephemeral=True)
    
        if count >= QUEUE_SIZE:
            if queue_task:
                queue_task.cancel()
            await start_match(interaction.channel)

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

async def queue_timeout_task(channel):
    global queue_message

    try:
        await discord.utils.sleep_until(
            discord.utils.utcnow() + discord.utils.timedelta(seconds=QUEUE_TIMEOUT)
        )
    except:
        return

    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM queue")

        if count < QUEUE_SIZE:
            await conn.execute("DELETE FROM queue")

            if queue_message:
                await queue_message.edit(
                    content="❌ Fila cancelada. Tempo esgotado."
                )

            queue_message = None

async def start_match(channel):
    global queue_message

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT p.user_id, p.discord_name, p.mmr
            FROM queue q
            JOIN players p ON p.user_id = q.user_id
            ORDER BY p.mmr DESC
        """)

        await conn.execute("DELETE FROM queue")

    players = list(rows)

    team_a = []
    team_b = []

    for i, player in enumerate(players):
        if i % 4 in (0, 3):
            team_a.append(player)
        else:
            team_b.append(player)

    avg_a = sum(p["mmr"] for p in team_a) // len(team_a)
    avg_b = sum(p["mmr"] for p in team_b) // len(team_b)

    embed = discord.Embed(
        title="🔥 PARTIDA FORMADA 🔥",
        color=discord.Color.green()
    )

    embed.add_field(
        name=f"Radiant (Média {avg_a})",
        value="\n".join(f"{p['discord_name']} ({p['mmr']})" for p in team_a),
        inline=False
    )

    embed.add_field(
        name=f"Dire (Média {avg_b})",
        value="\n".join(f"{p['discord_name']} ({p['mmr']})" for p in team_b),
        inline=False
    )

    await channel.send(embed=embed)

    if queue_message:
        await queue_message.delete()

    queue_message = None

# =========================
# RUN
# =========================
bot.run(TOKEN)












