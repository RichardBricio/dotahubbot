import sys
import logging
import os
import gevent
from steam.client import SteamClient
from dota2.client import Dota2Client

# Silenciar logs para focar apenas no que o bot precisa ler
logging.basicConfig(level=logging.ERROR)
logging.getLogger('dota2').setLevel(logging.CRITICAL)
logging.getLogger('steam').setLevel(logging.CRITICAL)
logging.getLogger('dota2.socache').setLevel(logging.CRITICAL)

# --- CONFIGURAÇÕES ---
USER = os.getenv("STEAM_USER")
PASSWORD = os.getenv("STEAM_PASS")
LOBBY_PASSWORD = sys.argv[1] if len(sys.argv) > 1 else 'hub123'
STEAM_IDS_STR = sys.argv[2] if len(sys.argv) > 2 else ""
LOBBY_LIFETIME = 300 

def to_account_id(steam_id_64):
    try:
        return int(steam_id_64) & 0xFFFFFFFF
    except:
        return None

client = SteamClient()
dota = Dota2Client(client)
client.cm_list_bootstrap_timeout = 30

@client.on('logged_on')
def start_dota():
    print("STEAM_LOGGED_IN")
    dota.launch()

@dota.on('ready')
def ready():
    print("DOTA_READY")
    # Limpeza de lobbies fantasmas antes de criar um novo
    if dota.lobby:
        dota.leave_practice_lobby()
        gevent.sleep(2)
    create_lobby()

def create_lobby():
    options = {
        'game_name': 'DotaHub Match',
        'server_region': 10, # 10 = South America (Brazil)
        'game_mode': 2,      # 2 = Captains Mode (ou 1 para All Pick)
        'pass_key': LOBBY_PASSWORD,
        'allow_spectating': True,
        'allow_cheats': False,
        'fill_with_bots': False
    }
    dota.create_practice_lobby(password=LOBBY_PASSWORD, options=options)

@dota.on('lobby_new')
def on_lobby_new(lobby):
    # Entra no slot de Coach/Broadcaster para não ocupar vaga de player
    dota.join_practice_lobby_team(team=4) 
    
    # IMPORTANTE: Este print é o que o seu bot_prime.py lê
    print(f"LOBBY_LINK:steam://joinlobby/570/{lobby.lobby_id}/{client.steam_id.as_64}")
    
    gevent.sleep(2) 

    # --- LÓGICA DE CONVITES DIRETOS ---
    if STEAM_IDS_STR:
        ids_list = STEAM_IDS_STR.split(',')
        for s_id in ids_list:
            acc_id = to_account_id(s_id)
            if acc_id:
                # Envia o pop-up de convite dentro do jogo
                dota.invite_to_lobby(acc_id)
                print(f"INVITE_SENT:{acc_id}")
                gevent.sleep(0.3)
    
    # Se ninguém entrar em 5 minutos, o processo morre para não gastar recursos
    gevent.spawn_later(LOBBY_LIFETIME, os._exit, 0)

@dota.on('lobby_changed')
def on_lobby_changed(lobby):
    try:
        # Verifica se já existem humanos no lobby
        humanos = [m for m in lobby.all_members if m.id != client.steam_id]
    except AttributeError:
        # Fallback caso a estrutura do objeto varie entre versões
        return
    
    if len(humanos) >= 1:
        # Quando o primeiro jogador entrar, o bot sai e deixa ele como dono
        print(f"LOBBY_ESTAVEL: Jogador {humanos[0].id} assumiu.")
        
        try:
            # O dota2.client transfere o host automaticamente ao sair
            dota.leave_practice_lobby()
            gevent.sleep(1)
            client.disconnect()
            gevent.sleep(1) 
            print("BOT_OFFLINE_SUCESSO")
        except:
            pass
        finally:
            os._exit(0)

if __name__ == "__main__":
    # Aumentamos drasticamente o timeout para 30 segundos
    client.cm_list_bootstrap_timeout = 30
    try:
        if client.connect():
            print("Conectado à rede Steam. Autenticando...")
            client.login(username=USER, password=PASSWORD)
            client.run_forever()
        else:
            print("FALHA_CONEXAO_STEAM")
            sys.exit(1)
    except KeyboardInterrupt:
        os._exit(0)