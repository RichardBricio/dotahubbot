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
import datetime

# --- CONFIGURAÇÕES ---
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

# =========================
# ARQUITETURA SAAS / ESTADO
# =========================
class ServerState:
    def __init__(self):
        self.queues = {}  # Dicionário: {owner_id: queue_message_object}
        self.active_matches = {} # Dicionário: {owner_id: process_object}
        self.queue_message = None
        self.queue_task = None
        self.queue_started_at = None
        self.process = None

class DotaBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.estados = {}
        self.pool = None

    def get_state(self, guild_id):
        if guild_id not in self.estados:
            self.estados[guild_id] = ServerState()
        return self.estados[guild_id]

    def get_season(self):
        return datetime.datetime.now().strftime("%Y-%m")

    async def setup_hook(self):
        # Inicializa o pool na instância do bot
        self.pool = await asyncpg.create_pool(DATABASE_URL)
        async with self.pool.acquire() as conn:
            # Tabelas corrigidas com guild_id e season_id para SaaS
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    user_id BIGINT, guild_id BIGINT, season_id TEXT,
                    discord_name TEXT, dota_nick TEXT, medal TEXT,
                    mmr INTEGER DEFAULT 0, wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0, points INTEGER DEFAULT 0,
                    steam_id_64 BIGINT, steam_url TEXT,
                    PRIMARY KEY (user_id, guild_id, season_id)
                );
                CREATE TABLE IF NOT EXISTS queue (
                    user_id BIGINT, guild_id BIGINT,
                    joined_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (user_id, guild_id)
                );
            """)
        await self.tree.sync()
        print(f"✅ Bot {self.user} Online e Sincronizado!")

    async def limpar_canais_voz(self, guild):
            categoria_dota = next((cat for cat in guild.categories if "dota 2" in cat.name.lower()), None)
            if not categoria_dota: return
            
            # Identifica canal para mover jogadores de volta
            canais_candidatos = [vc for vc in categoria_dota.voice_channels if vc.name not in ["🟢 RADIANT", "🔴 DIRE"]]
            target = max(canais_candidatos, key=lambda vc: len(vc.members)) if canais_candidatos else None

            for name in ["🟢 RADIANT", "🔴 DIRE"]:
                channel = discord.utils.get(categoria_dota.voice_channels, name=name)
                if channel:
                    if target:
                        for m in channel.members:
                            try: await m.move_to(target)
                            except: pass
                    await channel.delete()

bot = DotaBot()

# =========================
# LÓGICA DE STEAM E DADOS
# =========================
async def buscar_dados_steam(url_usuario):
    url_xml = url_usuario.rstrip('/') + "?xml=1"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url_xml, timeout=10) as resp:
                if resp.status != 200: return None
                root = ET.fromstring(await resp.text())
                return {
                    "steam_id": int(root.find('steamID64').text),
                    "nickname": root.find('steamID').text
                }
    except: return None

async def encerrar_fila_global(it: discord.Interaction, owner_id):
    state = bot.get_state(it.guild.id)
    
    # 1. Limpa o banco de dados de quem estava na fila
    async with bot.pool.acquire() as conn:
        await conn.execute("DELETE FROM queue WHERE guild_id = $1", it.guild.id)
    
    # 2. Mata o processo do lobby se ele existir
    process = state.active_matches.get(owner_id)
    if process:
        try: process.terminate()
        except: pass
        del state.active_matches[owner_id]

    # 3. Remove a entrada do dicionário de filas
    if owner_id in state.queues:
        del state.queues[owner_id]

    # 4. Tenta apagar a mensagem atual do modal
    try:
        await it.message.delete()
    except:
        pass
    
    # 5. Limpa canais de voz se houver
    await bot.limpar_canais_voz(it.guild)
    
    await it.response.send_message("🛑 Fila e processos encerrados com sucesso.", ephemeral=True)

# =========================
# VIEWS E MODAIS (SUA PERSONALIZAÇÃO)
# =========================
class CadastroModal(discord.ui.Modal, title="Cadastro DotaHub"):
    steam_url = discord.ui.TextInput(label="Link do seu Perfil Steam", min_length=20)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        dados = await buscar_dados_steam(self.steam_url.value)
        if not dados: return await interaction.followup.send("❌ Perfil não encontrado.", ephemeral=True)

        view = discord.ui.View()
        select = discord.ui.Select(placeholder="Selecione sua Medalha")
        for m in MEDAL_MMR.keys():
            select.add_option(label=m)
        
        async def select_callback(it: discord.Interaction):
            medal = select.values[0]
            
            # CORREÇÃO DEFINITIVA: 
            # Como o 'bot_instance' foi passado no __init__ e está disponível 
            # no escopo de 'on_submit', usamos 'bot_instance.pool'
            async with bot_instance.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO players (user_id, guild_id, discord_name, dota_nick, medal, mmr, points, steam_id_64, steam_url) 
                    VALUES ($1, $2, $3, $4, $5, $6, 0, $7, $8)
                    ON CONFLICT (user_id, guild_id) 
                    DO UPDATE SET dota_nick = $4, medal = $5, mmr = $6
                """, it.user.id, it.guild.id, it.user.display_name, dados['nickname'], medal, MEDAL_MMR[medal], dados['steam_id'], self.steam_url.value)
            
            await it.response.send_message(f"✅ Cadastro concluído como **{dados['nickname']}**!", ephemeral=True)
            
            # Chama a função de entrada na fila para atualizar o contador (ex: 1/10)
            await add_to_queue(it)

        select.callback = select_callback
        view.add_item(select)
        await interaction.followup.send("Steam confirmada! Selecione sua medalha:", view=view, ephemeral=True)

class ResultadoView(discord.ui.View):
    def __init__(self, teamA, teamB, join_link, avg_geral, owner_id):
        super().__init__(timeout=None)
        # SALVAMOS OS DICIONÁRIOS COMPLETOS AQUI
        self.teamA = teamA 
        self.teamB = teamB
        self.join_link = join_link
        self.avg_geral = avg_geral
        self.owner_id = owner_id

        self.add_item(discord.ui.Button(
            label="🚀 ENTRAR NO JOGO", 
            url=join_link, 
            style=discord.ButtonStyle.link,
            row=0
        ))

    @discord.ui.button(label="🏆 Radiant Venceu", style=discord.ButtonStyle.success)
    async def rad_win(self, it: discord.Interaction, btn: discord.ui.Button):
        await self.processar_vitoria(it, self.teamA, self.teamB, "RADIANT")

    @discord.ui.button(label="🏆 Dire Venceu", style=discord.ButtonStyle.danger)
    async def dire_win(self, it: discord.Interaction, btn: discord.ui.Button):
        await self.processar_vitoria(it, self.teamB, self.teamA, "DIRE")

    async def processar_vitoria(self, it, winners, losers, side):
        await it.response.defer()
        
        if not it.user.guild_permissions.administrator: 
            return await it.followup.send("Apenas administradores podem definir o vencedor.", ephemeral=True)
        
        # 1. Extraímos os IDs para a query do banco de dados (winners e losers já devem ser as listas de dicts)
        winners_ids = [p['user_id'] for p in winners]
        losers_ids = [p['user_id'] for p in losers]

        season = bot.get_season()
        async with bot.pool.acquire() as conn:
            await conn.execute("UPDATE players SET wins = wins + 1, points = points + 3 WHERE user_id = ANY($1) AND guild_id = $2 AND season_id = $3", winners_ids, it.guild.id, season)
            await conn.execute("UPDATE players SET losses = losses + 1 WHERE user_id = ANY($1) AND guild_id = $2 AND season_id = $3", losers_ids, it.guild.id, season)
        
        state = bot.get_state(it.guild.id)

        # 2. Finaliza o processo do lobby
        process = state.active_matches.get(self.owner_id)
        if process:
            try:
                process.terminate()
                del state.active_matches[self.owner_id]
            except Exception as e:
                print(f"Erro ao encerrar processo do lobby: {e}")

        if self.owner_id in state.queues:
            del state.queues[self.owner_id]

        # 3. Preparação do Embed de Histórico
        embed = it.message.embeds[0]
        embed.title = f"🏁 PARTIDA ENCERRADA - VITÓRIA: {side}"
        embed.description = f"⭐ **MMR Médio: `{int(self.avg_geral)}`**\n✅ Resultado registrado no Ranking."
        embed.color = discord.Color.dark_grey() 
        embed.clear_fields()

        # FUNÇÃO CORRIGIDA: Agora espera receber a lista de dicionários
        def format_team_with_medals(team_data):
            lines = []
            for p in team_data:
                # Busca o emoji pelo nome da medalha contido no dicionário 'p'
                emoji = discord.utils.get(bot.emojis, name=p['medal']) or "🏅"
                lines.append(f"{emoji} {p['discord_name']}")
            return "\n".join(lines) if lines else "---"

        # AJUSTE: Passamos self.teamA e self.teamB (que devem ser as listas de dicts salvas no __init__)
        embed.add_field(name="🟢 RADIANT", value=format_team_with_medals(self.teamA), inline=True)
        embed.add_field(name="🔴 DIRE", value=format_team_with_medals(self.teamB), inline=True)
        embed.set_footer(text="Partida finalizada e computada no ranking.")

        # Remove botões e atualiza a mensagem
        await it.edit_original_response(content=None, embed=embed, view=None)
        await bot.limpar_canais_voz(it.guild)

    @discord.ui.button(label="⚠️ Abortar Partida", style=discord.ButtonStyle.secondary, row=1)
    async def abort(self, it: discord.Interaction, button: discord.ui.Button):
        if not it.user.guild_permissions.administrator:
            return await it.response.send_message("Apenas ADMs.", ephemeral=True)
        await encerrar_fila_global(it, self.owner_id)

class PreMatchView(discord.ui.View):
    def __init__(self, tA, tB, pw, lobby_name, avg_geral, owner_id):
        super().__init__(timeout=None)
        self.tA = tA
        self.tB = tB
        self.pw = pw
        self.lobby_name = lobby_name
        self.avg_geral = avg_geral
        self.owner_id = owner_id
        # O link dinâmico que será atualizado no método criar
        self.dynamic_join_link = "https://www.google.com"

    @discord.ui.button(label="🚀 CRIAR LOBBY NO DOTA", style=discord.ButtonStyle.primary)
    async def criar(self, it: discord.Interaction, btn: discord.ui.Button):
        if not it.user.guild_permissions.administrator: 
            return await it.response.send_message("Apenas administradores.", ephemeral=True)
        
        # 1. LIMPEZA IMEDIATA E FEEDBACK
        # Deletamos o modal anterior na hora para o usuário sentir que a etapa mudou.
        await it.message.delete()
        # Resposta efêmera para o ADM saber que o bot recebeu o comando.
        await it.response.send_message("⌛ Preparando arena e movendo jogadores...", ephemeral=True)

        # 2. LOGÍSTICA DE CANAIS DE VOZ (Interface física)
        # Fazemos isso antes do Steam porque é instantâneo e dá feedback visual aos jogadores.
        v_rad, v_dire = None, None
        try:
            categoria = next((cat for cat in it.guild.categories if "dota 2" in cat.name.lower()), it.channel.category)
            v_rad = await it.guild.create_voice_channel("🟢 RADIANT", category=categoria)
            v_dire = await it.guild.create_voice_channel("🔴 DIRE", category=categoria)

            # Movemos em tasks separadas para não travar o código se alguém estiver offline/lagado
            for p in self.tA:
                m = it.guild.get_member(p['user_id'])
                if m and m.voice: asyncio.create_task(m.move_to(v_rad))
            for p in self.tB:
                m = it.guild.get_member(p['user_id'])
                if m and m.voice: asyncio.create_task(m.move_to(v_dire))
        except Exception as e:
            print(f"Erro na criação/movimentação de canais: {e}")

        # 3. TRABALHO PESADO (Database e Subprocesso Steam)
        async with bot.pool.acquire() as conn:
            ids = [p['user_id'] for p in self.tA + self.tB]
            rows = await conn.fetch("SELECT steam_id_64 FROM players WHERE user_id = ANY($1) AND guild_id = $2", ids, it.guild.id)
            steam_list = ",".join([str(r['steam_id_64']) for r in rows if r['steam_id_64']])

        process = await asyncio.create_subprocess_exec(
            sys.executable, "lobby_manager.py", self.pw, steam_list,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        
        state = bot.get_state(it.guild.id)
        state.active_matches[self.owner_id] = process 
        
        if self.owner_id in state.queues:
            del state.queues[self.owner_id]

        # URL da sua GitHub Page (Sem o /blob/main/...)
        REDIRECIONADOR = "https://richardbricio.github.io/dotahubbot/"
        
        async def monitor_logs():
            while True:
                line = await process.stdout.readline()
                if not line: break
                text = line.decode('utf-8', errors='ignore').strip()

                if "Unsupported type" in text or "Dota2Client.socache" in text:
                    continue
                if "LOBBY_LINK:" in text:
                    # Captura o link bruto: steam://joinlobby/570/...
                    raw_link = text.replace("LOBBY_LINK:", "").strip()
                    # LINK DO GITHUB PAGES (Hospedado, não o código bruto)
                    self.dynamic_join_link = f"{REDIRECIONADOR}?join={raw_link}"
                    print(f"✅ Link de convite gerado: {self.dynamic_join_link}")
                print(f"[Lobby Log]: {text}")

        asyncio.create_task(monitor_logs())

        # --- LOOP DE ESPERA CRÍTICO ---
        # Aguarda até 15 segundos pelo link da Steam antes de enviar a mensagem
        for _ in range(15):
            if self.dynamic_join_link and "steam://" in self.dynamic_join_link:
                break
            await asyncio.sleep(1)

        # Se após 15s ainda não houver link, define um aviso em vez do Google
        if not self.dynamic_join_link:
            self.dynamic_join_link = REDIRECIONADOR # Abre apenas a página inicial

        # Fallback caso a Steam demore demais
        if not self.dynamic_join_link:
            self.dynamic_join_link = "⚠️ Link em geração... verifique o canal em instantes."

        # 5. CÁLCULO DE MÉDIAS E PREPARAÇÃO DO EMBED FINAL
        # Fazemos isso enquanto o monitor_logs "escuta" o link da Steam
        await asyncio.sleep(1.5) # Pequena pausa para estabilizar

        avgA = sum(p['mmr'] for p in self.tA) / len(self.tA) if self.tA else 0
        avgB = sum(p['mmr'] for p in self.tB) / len(self.tB) if self.tB else 0
        # avg_geral = (avgA + avgB) / 2 if (avgA + avgB) > 0 else 0

        # Cores baseadas no nível da partida (opcional, deixa mais premium)
        cor = discord.Color.blue()
        if self.avg_geral > 5000: cor = discord.Color.purple()
        elif self.avg_geral > 3500: cor = discord.Color.gold()

        def formatar_lista(team):
            return "\n".join([f"{(discord.utils.get(bot.emojis, name=p['medal']) or '🏅')} • {p['discord_name']}" for p in team]) or "Vazio"

        embed = discord.Embed(title="🎮 LOBBY PRONTO - BOA SORTE!", color=cor)
        embed.description = (
            f"⭐ **MMR Médio da Partida: `{int(self.avg_geral)}`**\n"
            f"🚀 **[CLIQUE AQUI PARA ENTRAR NO LOBBY]({self.dynamic_join_link})**"
        )
        embed.add_field(name=f"🟢 RADIANT (AVG: {int(avgA)})", value=formatar_lista(self.tA), inline=True)
        embed.add_field(name=f"🔴 DIRE (AVG: {int(avgB)})", value=formatar_lista(self.tB), inline=True)
        embed.set_footer(text=f"Partida de '{it.user.display_name}'\nSala: {self.lobby_name} | Senha: {self.pw}")

        # 6. ENTREGA FINAL
        res_view = ResultadoView(
            teamA=self.tA, 
            teamB=self.tB,
            avg_geral=self.avg_geral,
            join_link=self.dynamic_join_link,
            owner_id=self.owner_id 
        )

        user_create_fila = self.owner_id if self.owner_id else it.user.id
        await it.channel.send(content=f"✅ **Lobby de <@{user_create_fila}> iniciado!**", embed=embed, view=res_view)

    @discord.ui.button(label="⚠️ Cancelar Confronto", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, it: discord.Interaction, button: discord.ui.Button):
        if not it.user.guild_permissions.administrator:
            return await it.response.send_message("Apenas ADMs.", ephemeral=True)
        await encerrar_fila_global(it, self.owner_id)

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
        # Aviso de processamento
        await interaction.response.defer(ephemeral=True)
        
        dados = await buscar_dados_steam(self.steam_url.value)
        if not dados:
            return await interaction.followup.send(
                "❌ Perfil não encontrado! Certifique-se de que o link está correto e o perfil é público.", 
                ephemeral=True
            )

        view = discord.ui.View()
        
        # Gerando as opções com os emojis do bot
        options = []
        for m in MEDAL_MMR.keys():
            emoji = discord.utils.get(self.bot_instance.emojis, name=m)
            options.append(discord.SelectOption(label=m, emoji=emoji))

        select = discord.ui.Select(
            placeholder=f"Olá {dados['nickname']}, escolha sua Medalha", 
            options=options
        )
        
        async def select_callback(it: discord.Interaction):
            medal = select.values[0]
            season = self.bot_instance.get_season() # Obtém a temporada atual (Ex: 2024-03)
            
            async with self.bot_instance.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO players (user_id, guild_id, season_id, discord_name, dota_nick, medal, mmr, points, steam_id_64, steam_url) 
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 0, $8, $9)
                    ON CONFLICT (user_id, guild_id, season_id) 
                    DO UPDATE SET dota_nick = $5, medal = $6, mmr = $7
                """, 
                it.user.id,        # $1
                it.guild.id,       # $2
                season,            # $3
                it.user.display_name, # $4
                dados['nickname'], # $5
                medal,             # $6
                MEDAL_MMR[medal],  # $7
                dados['steam_id'], # $8
                self.steam_url.value # $9
                )
            
            await it.response.send_message(f"✅ Cadastro concluído como **{dados['nickname']}**!", ephemeral=True)
            await add_to_queue(it)

        select.callback = select_callback
        view.add_item(select)
        
        await interaction.followup.send("Steam confirmada! Agora selecione sua medalha atual:", view=view, ephemeral=True)

# =========================
# COMANDOS DE FILA
# =========================
async def add_to_queue(it: discord.Interaction):
    state = bot.get_state(it.guild.id)
    season = bot.get_season()
    
    async with bot.pool.acquire() as conn:
        # 1. Garante que o jogador está na fila
        exists = await conn.fetchval("SELECT 1 FROM queue WHERE user_id = $1 AND guild_id = $2", it.user.id, it.guild.id)
        if not exists:
            await conn.execute("INSERT INTO queue (user_id, guild_id) VALUES ($1, $2)", it.user.id, it.guild.id)

        # 2. Busca contagem e lista atualizada DENTRO da mesma conexão
        rows = await conn.fetch("""
            SELECT p.discord_name FROM queue q 
            JOIN players p ON p.user_id = q.user_id AND p.guild_id = q.guild_id 
            WHERE q.guild_id = $1 AND p.season_id = $2
        """, it.guild.id, season)
        
        count = len(rows)

    # 3. Responde ao clique (Sucesso individual)
    if not it.response.is_done():
        await it.response.send_message(f"✅ Você entrou na fila! ({count}/{QUEUE_SIZE})", ephemeral=True)

    # 4. Atualiza o modal público (Nicks, Contador e Medalhas)
    await atualizar_mensagem_fila(it.guild)
    
    # 5. DISPARO ÚNICO DO CONFRONTO
    # Usamos == para evitar duplicação em processos paralelos
    if count == QUEUE_SIZE:
        await asyncio.sleep(0.5) 
        await iniciar_confronto(it.channel)

class QueueView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=None)
        self.owner_id = owner_id

    @discord.ui.button(label="Entrar na Fila", style=discord.ButtonStyle.success, custom_id="join_queue", row=0)
    async def join(self, it: discord.Interaction, button: discord.ui.Button):
        async with bot.pool.acquire() as conn:
            player = await conn.fetchrow("SELECT 1 FROM players WHERE user_id=$1 AND guild_id=$2", it.user.id, it.guild.id)
            if not player:
                return await it.response.send_modal(CadastroModal(bot))         
        await add_to_queue(it)

    @discord.ui.button(label="Sair da Fila", style=discord.ButtonStyle.danger, custom_id="leave_queue", row=0)
    async def leave(self, it: discord.Interaction, button: discord.ui.Button):
        async with bot.pool.acquire() as conn:
            await conn.execute("DELETE FROM queue WHERE user_id = $1 AND guild_id = $2", it.user.id, it.guild.id)
        
        if not it.response.is_done():
            await it.response.send_message("Você saiu da fila.", ephemeral=True)
        await atualizar_mensagem_fila(it.guild)

    @discord.ui.button(label="⚠️ Encerrar Fila", style=discord.ButtonStyle.secondary, custom_id="stop_queue", row=0)
    async def stop(self, it: discord.Interaction, button: discord.ui.Button):
        if not it.user.guild_permissions.administrator:
            return await it.response.send_message("Apenas administradores podem encerrar a fila.", ephemeral=True)
        
        await encerrar_fila_global(it, self.owner_id)

async def atualizar_mensagem_fila(guild):
    state = bot.get_state(guild.id)
    
    # Tenta pegar a mensagem do estado global ou da primeira fila ativa
    msg = state.queue_message or next(iter(state.queues.values()), None)
    
    if not msg:
        print("DEBUG: Mensagem de fila não encontrada no estado.")
        return

    season = bot.get_season()
    async with bot.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT p.discord_name, p.medal FROM queue q 
            JOIN players p ON p.user_id = q.user_id AND p.guild_id = q.guild_id 
            WHERE q.guild_id = $1 AND p.season_id = $2
            ORDER BY q.joined_at ASC
        """, guild.id, season)
    
    count = len(rows)
    lista_jogadores = ""
    for r in rows:
        # Busca o emoji pelo nome da medalha (Herald, Guardian, etc)
        emoji = discord.utils.get(bot.emojis, name=r['medal']) or "🏅"
        lista_jogadores += f"{emoji} **{r['discord_name']}**\n"
    
    if not lista_jogadores:
        lista_jogadores = "Aguardando jogadores..."

    embed = discord.Embed(
        title="🎮 DOTAHUB - Matchmaking",
        description="Selecione 'Entrar na Fila' para participar!",
        color=discord.Color.blue()
    )
    # Atualiza o contador dinâmico (ex: 1/2 conforme sua imagem)
    embed.add_field(name="Jogadores", value=f"👥 **{count}/{QUEUE_SIZE}**", inline=False)
    embed.add_field(name="👥 Lista Atual", value=lista_jogadores, inline=False)
    
    try:
        # Recupera o owner_id para manter a View funcional
        owner_id = next((oid for oid, m in state.queues.items() if m.id == msg.id), None)
        await msg.edit(embed=embed, view=QueueView(owner_id=owner_id))
    except Exception as e:
        print(f"Erro ao editar modal de fila: {e}")

# Função auxiliar para atualizar a mensagem da fila quando alguém sai
async def iniciar_confronto(channel):
    state = bot.get_state(channel.guild.id)
    season = bot.get_season()

    # 1. CAPTURA O DONO ANTES DE DELETAR TUDO
    current_owner = next(iter(state.queues.keys()), None)

    # MELHORIA 1: Apaga o modal de FILA específico desta partida
    # Buscamos a mensagem no dicionário de filas ativas
    for owner_id in list(state.queues.keys()):
        msg = state.queues[owner_id]
        try:
            await msg.delete()
        except Exception as e:
            print(f"Erro ao deletar msg de fila: {e}")
        # Remove do dicionário para liberar o usuário para uma nova fila futura
        del state.queues[owner_id]

    # Limpa também a referência genérica se ela existir
    if state.queue_message:
        try: await state.queue_message.delete()
        except: pass
        state.queue_message = None

    async with bot.pool.acquire() as conn:
        # Busca dados dos jogadores para o balanceamento
        players_data = await conn.fetch("""
            SELECT p.user_id, p.discord_name, p.mmr, p.medal, p.steam_id_64 
            FROM queue q
            JOIN players p ON p.user_id = q.user_id AND p.guild_id = q.guild_id
            WHERE q.guild_id = $1 AND p.season_id = $2
        """, channel.guild.id, season)
        
        # Se por algum motivo a função for chamada com a fila vazia (duplicação), interrompe
        if len(players_data) < QUEUE_SIZE:
            return

        # Limpa a fila IMEDIATAMENTE para evitar que outros processos entrem aqui
        await conn.execute("DELETE FROM queue WHERE guild_id = $1", channel.guild.id)

    # Balanceamento por MMR
    players = sorted(list(players_data), key=lambda x: x["mmr"], reverse=True)
    tA, tB = [], []
    for p in players:
        if sum(x["mmr"] for x in tA) <= sum(x["mmr"] for x in tB):
            tA.append(p)
        else:
            tB.append(p)

    # Cálculo de médias
    all_mmrs = [p['mmr'] for p in players_data]
    avg_geral = sum(all_mmrs) / len(all_mmrs) if all_mmrs else 0

    # Cálculo das médias (Mantenha como está)
    avgA = sum(p['mmr'] for p in tA) / len(tA) if tA else 0
    avgB = sum(p['mmr'] for p in tB) / len(tB) if tB else 0
    pw = f"hub{random.randint(100,999)}"
    lobby_name = "DotaHub Match" # MELHORIA 1: Nome da sala definido

    # Captura o dono da fila para vincular o processo
    current_owner = next(iter(state.queues.keys()), None)

    # Função para formatar times com medalhas
    def format_team(team):
        lines = []
        for p in team:
            emoji = discord.utils.get(bot.emojis, name=p['medal']) or "🏅"
            lines.append(f"{emoji} • {p['discord_name']}")
        return "\n".join(lines) if lines else "Vazio"

    # UNIFICAÇÃO DO EMBED (Corrigindo o erro de campos vazios)
    embed = discord.Embed(
        title="⚔️ CONFRONTO DEFINIDO ⚔️", 
        color=discord.Color.gold()
    )
    # Informação de MMR Médio Geral solicitada
    embed.description = f"📊 **MMR MÉDIO DA PARTIDA:** `{int(avg_geral)}`"
    embed.add_field(name=f"🟢 RADIANT (AVG: {int(avgA)})", value=format_team(tA), inline=True)
    embed.add_field(name=f"🔴 DIRE (AVG: {int(avgB)})", value=format_team(tB), inline=True)
    embed.set_footer(text=f"Equilíbrio baseado em MMR/Medalha real")

    # Identificar o dono da fila (owner_id)
    state = bot.get_state(channel.guild.id)
    current_owner = None
    for owner in state.queues.keys():
        current_owner = owner
        break

    # CORREÇÃO: Passando todos os argumentos exigidos pelo __init__
    view = PreMatchView(
        tA=tA, 
        tB=tB, 
        pw=pw, 
        lobby_name=lobby_name,
        avg_geral=avg_geral, 
        owner_id=current_owner
    )
    
    await channel.send(content="🔥 **A PARTIDA VAI COMEÇAR!**", embed=embed, view=view)

@bot.tree.command(name="fila", description="Inicia a fila de matchmaking")
async def fila(it: discord.Interaction):
    # 1. Defer imediato para ganhar tempo (3 segundos de limite do Discord)
    try:
        await it.response.defer()
    except discord.errors.NotFound:
        print("⚠️ Interação expirou devido a alta latência.")
        return

    state = bot.get_state(it.guild.id)
    owner_id = it.user.id
    
    # 2. Validação de Fila Ativa / Fantasma
    if owner_id in state.queues:
        msg_antiga = state.queues[owner_id]
        try:
            # Tenta buscar a mensagem no canal para ver se ela ainda existe
            await it.channel.fetch_message(msg_antiga.id)
            # Se encontrou, a fila é real e está ativa
            return await it.followup.send("⚠️ Você já tem uma fila aberta! Finalize-a antes de abrir outra.", ephemeral=True)
        except:
            # Se caiu aqui, a mensagem foi apagada ou o bot perdeu a referência. 
            # Limpamos o estado para permitir uma nova criação.
            del state.queues[owner_id]

    # 3. Criação da Nova Fila
    embed = discord.Embed(
        title="🎮 DOTAHUB - Matchmaking",
        description=f"Fila iniciada por: {it.user.mention}\nSelecione 'Entrar na Fila'!",
        color=discord.Color.blue()
    )
    embed.add_field(name="Jogadores", value=f"0/{QUEUE_SIZE}")
    
    view = QueueView(owner_id=owner_id)
    
    try:
        msg = await it.followup.send(embed=embed, view=view)
        state.queues[owner_id] = msg
        state.queue_message = msg
    except Exception as e:
        print(f"❌ Erro ao enviar mensagem de fila: {e}")

if __name__ == "__main__":
    bot.run(TOKEN)