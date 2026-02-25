import os
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import asyncpg
from datetime import timedelta

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GUILD_ID = int(os.getenv("GUILD_ID"))

intents = discord.Intents.default()

pool = None
queue_message = None
queue_task = None
queue_started_at = None

# =========================
# CONFIG FILA
# =========================
QUEUE_SIZE = 4        # ALTERE PARA 10 EM PRODUÇÃO
QUEUE_TIMEOUT = 60   # 5 minutos
#QUEUE_SIZE = 10       # 10 players
#QUEUE_TIMEOUT = 300   # 5 minutos

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
# BOT
# =========================
class DotaHubBot(commands.Bot):

    async def setup_hook(self):
        global pool
        pool = await asyncpg.create_pool(DATABASE_URL)
        await create_tables()

        guild = discord.Object(id=GUILD_ID)
        #await self.tree.sync()
        self.tree.clear_commands(guild=guild)
        await self.tree.sync(guild=guild)

        print("Bot sincronizado com sucesso.")

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

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                user_id BIGINT PRIMARY KEY,
                joined_at TIMESTAMP DEFAULT NOW()
            );
        """)

# =========================
# CADASTRO
# =========================
class CadastroModal(discord.ui.Modal, title="Cadastro DotaHub"):

    def __init__(self):
        super().__init__()
        self.dota_nick = discord.ui.TextInput(
            label="Seu nickname no Dota",
            required=True
        )
        self.add_item(self.dota_nick)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Selecione sua medalha:",
            view=MedalSelectView(self.dota_nick.value),
            ephemeral=True
        )

class MedalSelect(discord.ui.Select):

    def __init__(self, dota_nick):
        self.dota_nick = dota_nick

        options = [
            discord.SelectOption(label=m) for m in MEDAL_MMR.keys()
        ]

        super().__init__(
            placeholder="Escolha sua medalha",
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
                    discord_name = EXCLUDED.discord_name,
                    dota_nick = EXCLUDED.dota_nick,
                    medal = EXCLUDED.medal,
                    mmr = EXCLUDED.mmr;
            """,
            interaction.user.id,
            interaction.user.display_name,
            self.dota_nick,
            medal,
            mmr)

        await interaction.response.send_message(
            "Cadastro concluído. Você entrou automaticamente na fila.",
            ephemeral=True
        )

        # Auto entrar na fila
        await add_player_to_queue(interaction)

class MedalSelectView(discord.ui.View):
    def __init__(self, dota_nick):
        super().__init__(timeout=60)
        self.add_item(MedalSelect(dota_nick))

# =========================
# FILA
# =========================
class FilaView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    # =========================
    # BOTÃO ENTRAR
    # =========================
    @discord.ui.button(label="Entrar na Fila", style=discord.ButtonStyle.green)
    async def entrar(self, interaction: discord.Interaction, button: discord.ui.Button):

        global queue_message, queue_task, queue_started_at

        async with pool.acquire() as conn:

            player = await conn.fetchrow(
                "SELECT * FROM players WHERE user_id = $1",
                interaction.user.id
            )

            if player is None:
                await interaction.response.send_modal(CadastroModal())
                return

            try:
                await conn.execute(
                    "INSERT INTO queue (user_id) VALUES ($1)",
                    interaction.user.id
                )
            except asyncpg.UniqueViolationError:
                await interaction.response.send_message(
                    "Você já está na fila.",
                    ephemeral=True
                )
                return

            rows = await conn.fetch("""
                SELECT p.discord_name
                FROM queue q
                JOIN players p ON p.user_id = q.user_id
                ORDER BY q.joined_at
            """)

        count = len(rows)

        if count == 1:
            queue_started_at = discord.utils.utcnow()
            queue_task = bot.loop.create_task(
                queue_timeout_task(interaction.channel)
            )

        nick_list = "\n".join(f"• {r['discord_name']}" for r in rows)

        content = (
            f"🔥 Fila rolando: {count}/{QUEUE_SIZE}\n"
            f"⏳ Tempo restante: {QUEUE_TIMEOUT // 60}:{QUEUE_TIMEOUT % 60:02d}\n\n"
            f"👥 Jogadores na fila:\n{nick_list}"
        )

        if queue_message is None:
            queue_message = await interaction.channel.send(content)
        else:
            await queue_message.edit(content=content)

        await interaction.response.send_message(
            "Você entrou na fila.",
            ephemeral=True
        )

        if count >= QUEUE_SIZE:

            if queue_task:
                queue_task.cancel()

            if queue_message:
                await queue_message.edit(
                    content="✅ Quantidade de players atingida! Equipes sendo calibradas..."
                )

            await start_match(interaction.channel)

    # =========================
    # BOTÃO ENCERRAR
    # =========================
    @discord.ui.button(label="Encerrar Fila", style=discord.ButtonStyle.red)
    async def encerrar(self, interaction: discord.Interaction, button: discord.ui.Button):

        global queue_message, queue_task, queue_started_at

        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM queue")

            if count == 0:
                await interaction.response.send_message(
                    "Não há fila ativa.",
                    ephemeral=True
                )
                return

            await conn.execute("DELETE FROM queue")

        if queue_task:
            queue_task.cancel()
            queue_task = None

        if queue_message:
            await queue_message.edit(
                content="🛑 Fila encerrada manualmente."
            )
            queue_message = None

        queue_started_at = None

        await interaction.response.send_message(
            "Fila encerrada com sucesso.",
            ephemeral=True
        )

async def add_player_to_queue(interaction):

    global queue_message, queue_task, queue_started_at

    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO queue (user_id) VALUES ($1)",
                interaction.user.id
            )
        except asyncpg.UniqueViolationError:
            await interaction.response.send_message(
                "Você já está na fila.",
                ephemeral=True
            )
            return

        count = await conn.fetchval("SELECT COUNT(*) FROM queue")

    # Se for o primeiro jogador
    if count == 1:
        queue_started_at = discord.utils.utcnow()
        queue_task = asyncio.create_task(queue_timeout_task(interaction.channel))

    await update_queue_message(interaction.channel, count)

    if not interaction.response.is_done():
        await interaction.response.send_message("Você entrou na fila.", ephemeral=True)

    if count >= QUEUE_SIZE:
        if queue_task:
            queue_task.cancel()
        await start_match(interaction.channel)

async def update_queue_message(channel, count):

    global queue_message, queue_started_at

    elapsed = (discord.utils.utcnow() - queue_started_at).total_seconds()
    time_left = max(0, QUEUE_TIMEOUT - int(elapsed))

    minutes = time_left // 60
    seconds = time_left % 60

    content = f"🔥 Fila rolando: {count}/{QUEUE_SIZE}\n⏳ Tempo restante: {minutes}:{seconds:02d}"

    if queue_message is None:
        queue_message = await channel.send(content)
    else:
        await queue_message.edit(content=content)

async def queue_timeout_task(channel):

    global queue_message, queue_started_at

    try:
        while True:

            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT p.discord_name
                    FROM queue q
                    JOIN players p ON p.user_id = q.user_id
                """)
                count = len(rows)

            if count >= QUEUE_SIZE:
                return

            elapsed = (discord.utils.utcnow() - queue_started_at).total_seconds()
            time_left = max(0, QUEUE_TIMEOUT - int(elapsed))

            if time_left <= 0:
                async with pool.acquire() as conn:
                    await conn.execute("DELETE FROM queue")

                if queue_message:
                    await queue_message.edit(
                        content="⏰ Tempo expirado! Fila encerrada por falta de jogadores."
                    )

                queue_message = None
                return

            minutes = time_left // 60
            seconds = time_left % 60

            nick_list = "\n".join(f"• {r['discord_name']}" for r in rows) if rows else "Ninguém ainda..."

            content = (
                f"🔥 Fila rolando: {count}/{QUEUE_SIZE}\n"
                f"⏳ Tempo restante: {minutes}:{seconds:02d}\n\n"
                f"👥 Jogadores na fila:\n{nick_list}"
            )

            if queue_message:
                await queue_message.edit(content=content)

            await asyncio.sleep(1)

    except asyncio.CancelledError:
        return

# =========================
# MATCHMAKING
# =========================
import random

async def start_match(channel):
    global queue_message

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT p.user_id, p.discord_name, p.mmr
            FROM queue q
            JOIN players p ON p.user_id = q.user_id
        """)

        await conn.execute("DELETE FROM queue")

    if not rows:
        await channel.send("Erro: fila vazia.")
        return

    players = list(rows)

    # 🔥 embaralha primeiro
    random.shuffle(players)

    # ordena por mmr depois
    players.sort(key=lambda x: x["mmr"], reverse=True)

    team_a = []
    team_b = []

    # algoritmo guloso equilibrado
    for player in players:
        sum_a = sum(p["mmr"] for p in team_a)
        sum_b = sum(p["mmr"] for p in team_b)

        if sum_a <= sum_b:
            team_a.append(player)
        else:
            team_b.append(player)

    avg_a = sum(p["mmr"] for p in team_a) // len(team_a)
    avg_b = sum(p["mmr"] for p in team_b) // len(team_b)
    diff = abs(avg_a - avg_b)

    embed = discord.Embed(
        title="🔥 PARTIDA FORMADA 🔥",
        description=f"⚖️ Diferença média de MMR: {diff}",
        color=discord.Color.green()
    )

    embed.add_field(
        name=f"🟢 Radiant (Média {avg_a})",
        value="\n".join(f"{p['discord_name']} ({p['mmr']})" for p in team_a),
        inline=False
    )

    embed.add_field(
        name=f"🔴 Dire (Média {avg_b})",
        value="\n".join(f"{p['discord_name']} ({p['mmr']})" for p in team_b),
        inline=False
    )

    await channel.send(embed=embed)

    if queue_message:
        await queue_message.delete()
        queue_message = None

# =========================
# SLASH COMMAND
# =========================
@bot.tree.command(name="fila", description="Abrir painel da fila")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def fila(interaction: discord.Interaction):

    embed = discord.Embed(
        title="DotaHub Ranked Queue",
        description="Clique abaixo para entrar na fila.",
        color=discord.Color.red()
    )

    await interaction.response.send_message(embed=embed, view=FilaView())

# =========================
# RUN
# =========================
bot.run(TOKEN)







