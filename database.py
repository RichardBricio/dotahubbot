import psycopg2
import os

DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def setup():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            user_id BIGINT PRIMARY KEY,
            discord_name TEXT,
            dota_nick TEXT,
            medal TEXT,
            mmr INTEGER DEFAULT 2000,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    conn.close()


# =========================
# PLAYER CRUD
# =========================

def add_player(user_id, discord_name, dota_nick, medal):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO players (user_id, discord_name, dota_nick, medal)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id) DO NOTHING;
    """, (user_id, discord_name, dota_nick, medal))

    conn.commit()
    conn.close()


def get_player(user_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT mmr, wins, losses, medal, dota_nick
        FROM players
        WHERE user_id = %s
    """, (user_id,))

    player = cursor.fetchone()
    conn.close()
    return player


def top10():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT user_id, mmr, wins, losses
        FROM players
        ORDER BY mmr DESC
        LIMIT 10
    """)

    data = cursor.fetchall()
    conn.close()
    return data


def update_win(user_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE players
        SET wins = wins + 1,
            mmr = mmr + 25
        WHERE user_id = %s
    """, (user_id,))

    conn.commit()
    conn.close()


def update_loss(user_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE players
        SET losses = losses + 1,
            mmr = mmr - 25
        WHERE user_id = %s
    """, (user_id,))

    conn.commit()
    conn.close()
