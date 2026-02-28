import sys
import logging
import os
import gevent
from steam.client import SteamClient
from dota2.client import Dota2Client

# Silenciar mensagens de pacotes não suportados e cache do Dota
logging.getLogger('dota2.socache').setLevel(logging.CRITICAL)
logging.getLogger('dota2.client').setLevel(logging.CRITICAL)
logging.getLogger('steam').setLevel(logging.CRITICAL)

# --- CONFIGURAÇÕES ---
USER = os.getenv("STEAM_USER")
PASSWORD = os.getenv("STEAM_PASS")
LOBBY_PASSWORD = sys.argv[1] if len(sys.argv) > 1 else 'hub123'
# Captura a lista de IDs enviada pelo bot2.py (o segundo argumento)
STEAM_IDS_STR = sys.argv[2] if len(sys.argv) > 2 else ""
LOBBY_LIFETIME = 300 

def to_account_id(steam_id_64):
    """Converte SteamID64 (64 bits) para AccountID (32 bits) exigido pelo Dota"""
    try:
        return int(steam_id_64) & 0xFFFFFFFF
    except:
        return None

# Silenciar logs desnecessários
logging.basicConfig(level=logging.INFO)
logging.getLogger('dota2').setLevel(logging.CRITICAL)
logging.getLogger('steam').setLevel(logging.CRITICAL)

client = SteamClient()
dota = Dota2Client(client)

@client.on('logged_on')
def start_dota():
    print("STEAM_LOGGED_IN")
    dota.launch()

@dota.on('ready')
def create_lobby():
    if dota.lobby:
        dota.leave_practice_lobby()
        gevent.sleep(1)
        
    options = {
        'game_name': 'DotaHub Match',
        'server_region': 10,
        'game_mode': 2,
        'pass_key': LOBBY_PASSWORD,
        'allow_spectating': True,
        'allow_cheats': False,
        'fill_with_bots': False
    }
    dota.create_practice_lobby(password=LOBBY_PASSWORD, options=options)

# No lobby_manager.py, atualize estas funções:

@dota.on('lobby_new')
def on_lobby_new(lobby):
    dota.join_practice_lobby_team(team=4)
    print(f"LOBBY_LINK:steam://joinlobby/570/{lobby.lobby_id}")
    
    gevent.sleep(3) # Tempo para o bot se estabilizar no lobby
    
    if STEAM_IDS_STR:
        ids_list = STEAM_IDS_STR.split(',')
        for s_id in ids_list:
            acc_id = to_account_id(s_id)
            if acc_id:
                # Convite direto via Coordenador de Jogo
                dota.invite_to_lobby(acc_id)
                print(f"INVITE_SENT:{acc_id}")
                gevent.sleep(0.3)
    
    gevent.spawn_later(LOBBY_LIFETIME, sys.exit)

@dota.on('lobby_changed')
def on_lobby_changed(lobby):
    # Lista apenas os membros que não são o próprio bot
    humanos = [m for m in lobby.members if m.id != client.steam_id]
    
    # Assim que entrar o primeiro jogador (ou mais), o bot sai
    if len(humanos) >= 1:
        print(f"PLAYER_JOINED: Passing Admin to {humanos[0].id}...")
        gevent.sleep(1) # Pequeno delay para garantir estabilidade
        dota.leave_practice_lobby() 
        print("BOT_LEFT_SUCCESSFULLY")
        gevent.spawn_later(2, sys.exit)

    # Caso a partida inicie por algum motivo antes do bot sair
    if lobby.state == 3:
        print("GAME_STARTED_EARLY")
        dota.leave_practice_lobby()
        gevent.spawn_later(2, sys.exit)

# --- EXECUÇÃO ---
print("DEBUG: Tentando login direto...")
try:
    result = client.login(username=USER, password=PASSWORD)
    if result != 1:
        print(f"LOGIN_ERROR: {repr(result)}")
        sys.exit(1)
        
    print("DEBUG: Login bem-sucedido, entrando no loop...")
    client.run_forever()
except Exception as e:
    print(f"FATAL_ERROR: {str(e)}")
    sys.exit(1)
