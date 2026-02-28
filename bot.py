import os
import asyncio
import discord
from discord.ext import commands
import asyncpg
import random
import sys
import aiohttp
import re
import xml.etree.ElementTree as ET

# =========================
# CONFIGURAÇÕES
# =========================
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
RANKING_CHANNEL_NAME = "dotahub_ranking"
QUEUE_SIZE = 10
QUEUE_TIMEOUT = 300 

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

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

pool = None
queue_message = None
queue_task = None
queue_started_at = None

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
                points INTEGER DEFAULT 0,
                steam_id_64 BIGINT UNIQUE,
                steam_url TEXT
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                user_id BIGINT PRIMARY KEY,
                guild_id BIGINT,
                joined_at TIMESTAMP DEFAULT NOW()
            );
        """)

async def buscar_dados_steam(url_usuario):
    # Converte links de ID ou Perfil para a versão XML que a Steam fornece publicamente
    if "profiles/" in url_usuario:
        url_xml = url_usuario.rstrip('/') + "?xml=1"
    elif "id/" in url_usuario:
        url_xml = url_usuario.rstrip('/') + "?xml=1"
    else:
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url_xml) as resp:
                if resp.status != 200: return None
                text = await resp.text()
                root = ET.fromstring(text)
                return {
                    "steam_id": int(root.find('steamID64').text),
                    "nickname": root.find('steamID').text
                }
    except:
        return None

async def atualizar_ranking_fixo(guild):
    """Gera um ranking visualmente rico e atualiza a mensagem fixa"""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT discord_name, medal, mmr, wins, losses, points,
            CASE WHEN (wins + losses) > 0 THEN (CAST(wins AS FLOAT) / (wins + losses)) * 100 ELSE 0 END as winrate
            FROM players 
            ORDER BY points DESC, winrate DESC, losses ASC 
            LIMIT 20
        """)

    if not rows: return

    channel = discord.utils.get(guild.text_channels, name="dotahub_ranking")
    if not channel:
        channel = await guild.create_text_channel("dotahub_ranking")

    embed = discord.Embed(
        title="🏆 QUADRO DE HONRA - DOTAHUB",
        description="Estatísticas atualizadas em tempo real.",
        color=0xFFD700
    )

    lines = []
    for i, row in enumerate(rows, 1):
        total = row['wins'] + row['losses']
        wr = (row['wins'] / total * 100) if total > 0 else 0
        # Tenta pegar o emoji da medalha pelo nome
        emoji = discord.utils.get(bot.emojis, name=row['medal']) or "🏅"
        pos = f"#{i}" if i > 3 else ["🥇", "🥈", "🥉"][i-1]
        
        lines.append(f"{pos} | **{row['discord_name']}** | {emoji} | Pts: `{row['points']}` | WR: `{wr:.1f}%`")

    embed.add_field(name="Ranking (Top 20)", value="\n".join(lines), inline=False)
    embed.set_footer(text="As pontuações são atualizadas ao fim de cada partida.")

    # Busca mensagem anterior do bot para editar e manter fixo
    msg_fixa = None
    async for message in channel.history(limit=10):
        if message.author == bot.user and message.embeds and "QUADRO DE HONRA" in message.embeds[0].title:
            msg_fixa = message
            break

    if msg_fixa:
        await msg_fixa.edit(embed=embed)
    else:
        await channel.send(embed=embed)

class DotaHubBot(commands.Bot):
    async def setup_hook(self):
        global pool
        pool = await asyncpg.create_pool(DATABASE_URL)
        await create_tables()
        await self.tree.sync()
        print(f"✅ Bot {self.user} Online!")

bot = DotaHubBot(command_prefix="!", intents=intents)

# =========================
# COMANDO DE MANUTENÇÃO (DONO)
# =========================
@bot.command()
@commands.is_owner() # Apenas o dono do bot (você) pode rodar
async def sync_global(ctx):
    """Limpa comandos locais e sincroniza globalmente"""
    msg = await ctx.send("⏳ Iniciando faxina nos comandos...")
    
    try:
        # 1. Limpa os comandos registrados especificamente NESTE servidor
        bot.tree.clear_commands(guild=ctx.guild)
        await bot.tree.sync(guild=ctx.guild)
        
        # 2. Sincroniza os comandos Globais
        await bot.tree.sync()
        
        await msg.edit(content="✅ **Faxina concluída!**\n- Comandos da guilda removidos.\n- Comandos globais sincronizados.\n*(Pode levar alguns minutos para o Discord atualizar o cache global)*")
    except Exception as e:
        await msg.edit(content=f"❌ Erro ao sincronizar: {e}")

# =========================
# VIEW 4: RESULTADOS E PONTUAÇÃO
# =========================
class ResultadoView(discord.ui.View):
    def __init__(self, ids_a, ids_b, join_link=None):
        super().__init__(timeout=None)
        self.ids_a = ids_a  # IDs do Time A (Radiant)
        self.ids_b = ids_b  # IDs do Time B (Dire)
        self.join_link = join_link

    async def encerrar_processo_partida(self, interaction):
        """Limpa a fila e desativa os botões para encerrar o ciclo"""
        global queue_message, queue_task
        
        # 1. Limpa a fila no banco de dados para o próximo jogo
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM queue WHERE guild_id = $1", interaction.guild.id)
        
        # ITEM 2: Mover jogadores de volta e excluir salas
        await self.limpar_canais_voz(interaction.guild)

        # 2. Cancela tarefas de timer pendentes
        if queue_task:
            queue_task.cancel()
            queue_task = None
            
        # 3. Reseta a variável da mensagem de fila
        queue_message = None

        # 4. Desativa todos os botões da mensagem de resultado
        for child in self.children:
            child.disabled = True
        
        await interaction.message.edit(view=self)

    async def limpar_canais_voz(self, guild):
        """Move os jogadores para a sala ideal e deleta as temporárias"""
        # BUSCA FLEXÍVEL: Procura categoria que contenha "Dota 2"
        categoria_dota = next((cat for cat in guild.categories if "dota 2" in cat.name.lower()), None)
        
        # Se houver a categoria achada, usa os canais dela. Caso contrário, usa todos do server.
        canais_busca = categoria_dota.voice_channels if categoria_dota else guild.voice_channels
        
        target_channel = None

        if canais_busca:
            # Filtra para não tentar mover as pessoas para os próprios canais que serão deletados
            canais_candidatos = [vc for vc in canais_busca if vc.name not in ["🟢 RADIANT", "🔴 DIRE"]]
            
            if canais_candidatos:
                # Prioridade: Canal com mais pessoas (ex: "Geral" ou "Espera")
                target_channel = max(canais_candidatos, key=lambda vc: len(vc.members))
                
                # Se todos estiverem vazios, pega o primeiro da lista
                if len(target_channel.members) == 0:
                    target_channel = canais_candidatos[0]

        # 3. Processo de movimentação e deleção
        for name in ["🟢 RADIANT", "🔴 DIRE"]:
            # IMPORTANTE: Busca o canal especificamente dentro da categoria se ela existir
            # Isso evita deletar canais com nomes iguais em outras categorias por erro
            channel = discord.utils.get(guild.voice_channels, name=name, category=categoria_dota)
            
            if channel:
                if target_channel:
                    for member in channel.members:
                        try:
                            # Move o jogador para o canal destino (na categoria Dota 2 ou Fallback)
                            await member.move_to(target_channel)
                            await asyncio.sleep(0.2) # Pequeno delay para estabilidade da API
                        except Exception as e:
                            print(f"Erro ao mover {member.name}: {e}")
                
                try:
                    await channel.delete()
                except Exception as e:
                    print(f"Erro ao deletar canal {name}: {e}")

    async def update_score(self, winners_ids, losers_ids):
        """Atualiza pontos e estatísticas no banco de dados"""
        async with pool.acquire() as conn:
            # VENCEDORES: +1 Vitória e +3 Pontos (coluna 'points')
            await conn.execute("""
                UPDATE players 
                SET wins = wins + 1, points = points + 3 
                WHERE user_id = ANY($1)
            """, winners_ids)
            
            # PERDEDORES: +1 Derrota
            await conn.execute("""
                UPDATE players 
                SET losses = losses + 1 
                WHERE user_id = ANY($1)
            """, losers_ids)

    @discord.ui.button(label="🏆 Radiant Venceu", style=discord.ButtonStyle.success, row=0)
    async def rad_win(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator: 
            return await interaction.response.send_message("Apenas admins!", ephemeral=True)
        
        # 1. Primeiro respondemos à interação para o Discord saber que recebemos o clique
        # Isso evita o erro de "Unknown Webhook" ou interação expirada
        await interaction.response.defer(ephemeral=True)

        # 2. Processa pontos
        await self.update_score(self.ids_a, self.ids_b)
        
        # 3. Encerra o processo (limpa fila, desativa botões)
        await self.encerrar_processo_partida(interaction)

        # ATUALIZAÇÃO AUTOMÁTICA DO RANKING FIXO
        await atualizar_ranking_fixo(interaction.guild)
        
        # 4. Edita a mensagem principal para o estado final
        await interaction.message.edit(content="🏁 **Fim de Jogo: Vitória do RADIANT!**", view=self)
        
        # 5. Envia a confirmação usando o followup agora que o defer foi feito
        await interaction.followup.send("✅ Partida encerrada. Radiant +3 pontos. Fila resetada!", ephemeral=True)

    @discord.ui.button(label="🏆 Dire Venceu", style=discord.ButtonStyle.danger, row=0)
    async def dire_win(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator: 
            return await interaction.response.send_message("Apenas admins!", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)

        await self.update_score(self.ids_b, self.ids_a)
        await self.encerrar_processo_partida(interaction)
        
        # ATUALIZAÇÃO AUTOMÁTICA DO RANKING FIXO
        await atualizar_ranking_fixo(interaction.guild)
        
        await interaction.message.edit(content="🏁 **Fim de Jogo: Vitória do DIRE!**", view=self)
        await interaction.followup.send("✅ Partida encerrada. Dire +3 pontos. Fila resetada!", ephemeral=True)

    @discord.ui.button(label="📋 Copiar Link", style=discord.ButtonStyle.secondary, row=1)
    async def copiar_link(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.join_link:
            await interaction.response.send_message(f"Link do Lobby:\n`{self.join_link}`", ephemeral=True)

# =========================
# VIEW 3: PRÉ-MATCH (ADMIN CRIA LOBBY)
# =========================
class PreMatchView(discord.ui.View):
    def __init__(self, tA, tB, pw):
        super().__init__(timeout=None)
        self.tA, self.tB, self.pw = tA, tB, pw

    @discord.ui.button(label="🚀 CRIAR LOBBY NO DOTA", style=discord.ButtonStyle.primary)
    async def criar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Apenas admins!", ephemeral=True)
        
        button.disabled = True
        button.label = "⏳ Iniciando Steam..."
        await interaction.response.edit_message(view=self)
        
        asyncio.create_task(self.run_steam(interaction))

    async def run_steam(self, interaction):
        # Pega os IDs de todos os jogadores escalados
        discord_ids = [p['user_id'] for p in self.tA] + [p['user_id'] for p in self.tB]
        
        # No bot2.py, dentro de run_steam:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT steam_id_64 FROM players WHERE user_id = ANY($1)", discord_ids)
            # Filtra IDs nulos e converte para string
            steam_ids = [str(r['steam_id_64']) for r in rows if r['steam_id_64']]
            steam_list = ",".join(steam_ids)

        # DEBUG para você ver no console se a lista está indo certa
        print(f"DEBUG: Enviando para o lobby_manager: {steam_list}")

        process = await asyncio.create_subprocess_exec(
            sys.executable, "-u", "lobby_manager.py", self.pw, steam_list,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        
        join_link = None
        
        try:
            while True:
                line = await process.stdout.readline()
                if not line: break
                
                text = line.decode().strip()
                print(f"LOG_STEAM: {text}") # Isso vai aparecer no seu terminal do VS Code/CMD

                if "LOBBY_LINK:" in text:
                    join_link = text.split("LOBBY_LINK:")[1]
                    break
                elif "❌" in text or "LOGIN_ERROR" in text:
                    # Se der erro, para o loop e avisa
                    await interaction.followup.send(f"Erro no servidor: {text}", ephemeral=True)
                    break
        except Exception as e:
            print(f"Erro na leitura do processo: {e}")

        if join_link:
            # Captura os IDs para a próxima View
            ids_radiant = [p['user_id'] for p in self.tA]
            ids_dire = [p['user_id'] for p in self.tB]

            # ITEM 3: Localizar a categoria (Obrigatório "Dota 2" no seu, flexível em outros)
            guild = interaction.guild
            categoria_alvo = next((cat for cat in guild.categories if "dota 2" in cat.name.lower()), None)

            # Se não achar a categoria "Dota 2", ele cria no mesmo lugar onde o comando foi dado
            if not categoria_alvo:
                categoria_alvo = interaction.channel.category

            # Cria os canais usando a categoria encontrada ou a atual
            rad_vc = await guild.create_voice_channel("🟢 RADIANT", category=categoria_alvo)
            dire_vc = await guild.create_voice_channel("🔴 DIRE", category=categoria_alvo)
            
            for p_id in ids_radiant:
                member = guild.get_member(p_id)
                if member and member.voice:
                    try: await member.move_to(rad_vc)
                    except: pass
            
            for p_id in ids_dire:
                member = guild.get_member(p_id)
                if member and member.voice:
                    try: await member.move_to(dire_vc)
                    except: pass

            # CORREÇÃO DO ERRO: Passando os argumentos reais para a ResultadoView
            res_view = ResultadoView(
                ids_a=ids_radiant, 
                ids_b=ids_dire, 
                join_link=join_link
            )
            
            embed = interaction.message.embeds[0]
            embed.title = "🎮 LOBBY PRONTO - BOA SORTE!"
            embed.color = discord.Color.blue()
            
            embed.add_field(
                name="📝 Informações da Partida", 
                value=f"**Nome:** `DotaHub Match`\n**Senha:** `{self.pw}`\n\n🚀 [CLIQUE PARA ENTRAR]({join_link})", 
                inline=False
            )
            
            await interaction.message.edit(content="✅ **Servidor Online!**", embed=embed, view=res_view)
        else:
            # Se saiu do loop sem link (erro), reativamos o botão para tentar de novo
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = False
                    item.label = "🚀 Tentar Novamente"
            await interaction.message.edit(content="⚠️ Falha ao criar lobby. Verifique o console do Bot.", view=self)

# =========================
# MATCHMAKING LÓGICA
# =========================
async def start_match(channel):
    global queue_message
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT p.user_id, p.discord_name, p.mmr FROM queue q
            JOIN players p ON p.user_id = q.user_id WHERE q.guild_id = $1
        """, channel.guild.id)
        await conn.execute("DELETE FROM queue WHERE guild_id = $1", channel.guild.id)

    if len(rows) < QUEUE_SIZE: return
    
    players = sorted(list(rows), key=lambda x: x["mmr"], reverse=True)
    tA, tB = [], []
    for p in players:
        if sum(x["mmr"] for x in tA) <= sum(x["mmr"] for x in tB): tA.append(p)
        else: tB.append(p)

    pw = f"hub{random.randint(100,999)}"
    
    # Prevenção de erro de divisão por zero (especialmente para testes com 1 player)
    avg_a = int(sum(p['mmr'] for p in tA)/len(tA)) if len(tA) > 0 else 0
    avg_b = int(sum(p['mmr'] for p in tB)/len(tB)) if len(tB) > 0 else 0

    embed = discord.Embed(title="⚔️ CONFRONTO DEFINIDO ⚔️", color=discord.Color.gold())
    
    # Trata exibição se um dos times estiver vazio
    val_a = "\n".join(f"• {p['discord_name']}" for p in tA) if tA else "Vazio"
    val_b = "\n".join(f"• {p['discord_name']}" for p in tB) if tB else "Vazio"
    
    embed.add_field(name=f"🟢 RADIANT (AVG: {avg_a})", value=val_a, inline=True)
    embed.add_field(name=f"🔴 DIRE (AVG: {avg_b})", value=val_b, inline=True)
    
    await channel.send(content="🔥 **Fila Cheia!**", embed=embed, view=PreMatchView(tA, tB, pw))
    
    if queue_message: 
        try: await queue_message.delete()
        except: pass
        queue_message = None

# =========================
# CADASTRO E FILA (PONTOS 1 E 2)
# =========================
class CadastroModal(discord.ui.Modal, title="Cadastro DotaHub"):
    steam_url = discord.ui.TextInput(
        label="Link do seu Perfil Steam", 
        placeholder="https://steamcommunity.com/id/seu_perfil/",
        min_length=20
    )

    def __init__(self, bot_instance):
        super().__init__()
        self.bot_instance = bot_instance

    async def on_submit(self, interaction: discord.Interaction):
        # Aviso de processamento (necessário pois a busca externa pode demorar)
        await interaction.response.defer(ephemeral=True)
        
        dados = await buscar_dados_steam(self.steam_url.value)
        if not dados:
            return await interaction.followup.send(
                "❌ Perfil não encontrado! Certifique-se de que o link está correto e o perfil é público.", 
                ephemeral=True
            )

        # Reutilizando seu layout de seleção de medalhas
        view = discord.ui.View()
        select = discord.ui.Select(
            placeholder=f"Olá {dados['nickname']}, escolha sua Medalha", 
            options=[
                discord.SelectOption(
                    label=m, 
                    emoji=discord.utils.get(self.bot_instance.emojis, name=m)
                ) for m in MEDAL_MMR.keys()
            ]
        )
        
        async def select_callback(it: discord.Interaction):
            medal = select.values[0]
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO players (user_id, discord_name, dota_nick, medal, mmr, points, steam_id_64, steam_url) 
                    VALUES ($1, $2, $3, $4, $5, 0, $6, $7)
                """, it.user.id, it.user.display_name, dados['nickname'], medal, MEDAL_MMR[medal], dados['steam_id'], self.steam_url.value)
            
            await it.response.send_message(f"✅ Cadastro concluído como **{dados['nickname']}**!", ephemeral=True)
            await add_to_queue(it)

        select.callback = select_callback
        view.add_item(select)
        
        # Envia o menu de medalhas que você gosta
        await interaction.followup.send("Steam confirmada! Agora selecione sua medalha atual:", view=view, ephemeral=True)

async def add_to_queue(interaction):
    global queue_message, queue_task, queue_started_at
    async with pool.acquire() as conn:
        try:
            await conn.execute("INSERT INTO queue (user_id, guild_id) VALUES ($1, $2)", interaction.user.id, interaction.guild_id)
        except:
            return await interaction.response.send_message("Já na fila!", ephemeral=True)
        
        rows = await conn.fetch("SELECT p.discord_name FROM queue q JOIN players p ON p.user_id=q.user_id WHERE q.guild_id=$1", interaction.guild_id)
    
    count = len(rows)
    if count == 1:
        queue_started_at = discord.utils.utcnow()
        queue_task = asyncio.create_task(queue_timer(interaction.channel, interaction.guild_id))

    nicks = "\n".join(f"• {r['discord_name']}" for r in rows)
    content = f"🔥 Fila: {count}/{QUEUE_SIZE}\n👥 Jogadores:\n{nicks}"
    
    if queue_message: await queue_message.edit(content=content)
    else: queue_message = await interaction.channel.send(content)
    
    if not interaction.response.is_done(): await interaction.response.send_message("Entrou!", ephemeral=True)
    if count >= QUEUE_SIZE:
        if queue_task: queue_task.cancel()
        await start_match(interaction.channel)

async def queue_timer(channel, guild_id):
    global queue_message
    await asyncio.sleep(QUEUE_TIMEOUT)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM queue WHERE guild_id = $1", guild_id)
    if queue_message: await queue_message.edit(content="⏰ Fila expirada.")
    queue_message = None

class FilaView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Entrar na Fila", style=discord.ButtonStyle.green)
    async def entrar(self, interaction, btn):
        async with pool.acquire() as conn:
            if not await conn.fetchrow("SELECT 1 FROM players WHERE user_id=$1", interaction.user.id):
                return await interaction.response.send_modal(CadastroModal(bot))
        await add_to_queue(interaction)

    @discord.ui.button(label="Encerrar Fila", style=discord.ButtonStyle.red)
    async def fechar(self, interaction, btn):
        if not interaction.user.guild_permissions.administrator: return
        async with pool.acquire() as conn: await conn.execute("DELETE FROM queue")
        if queue_message: await queue_message.edit(content="🛑 Encerrada.")
        await interaction.response.send_message("Fila limpa.", ephemeral=True)

@bot.tree.command(name="perfil", description="Mostra suas estatísticas no DotaHub")
async def perfil(it: discord.Interaction, usuario: discord.User = None):
    target = usuario or it.user
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM players WHERE user_id = $1", target.id)
    
    if not row:
        return await it.response.send_message(
            f"❌ {'Você' if target == it.user else target.display_name} ainda não tem cadastro.", 
            ephemeral=True
        )

    # Cálculo de Winrate
    total = row['wins'] + row['losses']
    winrate = (row['wins'] / total * 100) if total > 0 else 0

    embed = discord.Embed(title=f"📊 Perfil de {row['discord_name']}", color=discord.Color.blue())
    embed.add_field(name="🏅 Medalha", value=row['medal'], inline=True)
    embed.add_field(name="⚔️ MMR", value=f"`{row['mmr']}`", inline=True)
    embed.add_field(name="🎮 Nick Dota", value=row['dota_nick'], inline=True)
    embed.add_field(name="📈 Winrate", value=f"{winrate:.1f}% ({total} jogos)", inline=True)
    embed.add_field(name="✅ Vitórias", value=str(row['wins']), inline=True)
    embed.add_field(name="❌ Derrotas", value=str(row['losses']), inline=True)
    
    await it.response.send_message(embed=embed)

@bot.tree.command(name="ranking", description="Mostra o ranking completo por pontos e winrate")
async def ranking(it: discord.Interaction):
    async with pool.acquire() as conn:
        # Ordenação: 1º Pontos, 2º Winrate (calculado), 3º Menos Derrotas (losses ASC)
        rows = await conn.fetch("""
            SELECT discord_name, mmr, wins, losses, points,
            CASE 
                WHEN (wins + losses) > 0 THEN (CAST(wins AS FLOAT) / (wins + losses)) * 100 
                ELSE 0 
            END as winrate
            FROM players 
            ORDER BY points DESC, winrate DESC, losses ASC
        """)

    if not rows:
        return await it.response.send_message("O ranking está vazio por enquanto.", ephemeral=True)

    description = ""
    for i, row in enumerate(rows, 1):
        # Ícones para o pódio
        medalha = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"`{i}.`"
        
        # Dados da linha
        pts = row['points']
        wr = row['winrate']
        w = row['wins']
        l = row['losses']
        
        # Formatação: Medalha | Nome | Pontos | WR% | (W-L)
        description += f"{medalha} **{row['discord_name']}** — `{pts} pts` | `{wr:.1f}% WR` ({w}W-{l}L)\n"

    embed = discord.Embed(
        title="🏆 DOTAHUB RANKING OFICIAL", 
        description=description, 
        color=discord.Color.gold()
    )
    embed.set_footer(text="Critérios: Pontos > Winrate > Menos Derrotas")
    
    await it.response.send_message(embed=embed)

@bot.tree.command(name="fila", description="Abrir painel da fila")
async def cmd_fila(it):
    await it.response.send_message(embed=discord.Embed(title="DotaHub Queue"), view=FilaView())

bot.run(TOKEN)
