import sqlite3
from config import INITIAL_MMR

conn = sqlite3.connect("dotahub.db")
cursor = conn.cursor()

def setup():
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS players (
        user_id INTEGER PRIMARY KEY,
        mmr INTEGER DEFAULT {INITIAL_MMR},
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        last_match TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        winner INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS match_players (
        match_id INTEGER,
        user_id INTEGER,
        team INTEGER
    )
    """)

    conn.commit()

def register(user_id):
    cursor.execute("INSERT OR IGNORE INTO players (user_id) VALUES (?)", (user_id,))
    conn.commit()

def get_player(user_id):
    cursor.execute("SELECT mmr, wins, losses FROM players WHERE user_id=?", (user_id,))
    return cursor.fetchone()

def update_player(user_id, mmr_delta, win):
    if win:
        cursor.execute("UPDATE players SET wins=wins+1, mmr=mmr+? WHERE user_id=?", (mmr_delta, user_id))
    else:
        cursor.execute("UPDATE players SET losses=losses+1, mmr=mmr+? WHERE user_id=?", (mmr_delta, user_id))
    conn.commit()

def top_players():
    cursor.execute("SELECT user_id, mmr, wins, losses FROM players ORDER BY mmr DESC LIMIT 10")
    return cursor.fetchall()
