import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def setup():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            user_id BIGINT PRIMARY KEY,
            mmr INTEGER DEFAULT 1000,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0
        );
    """)

    conn.commit()
    cursor.close()
    conn.close()

def ensure_player(user_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO players (user_id)
        VALUES (%s)
        ON CONFLICT (user_id) DO NOTHING;
    """, (user_id,))

    conn.commit()
    cursor.close()
    conn.close()

def add_win(user_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE players
        SET wins = wins + 1,
            mmr = mmr + 25
        WHERE user_id = %s;
    """, (user_id,))

    conn.commit()
    cursor.close()
    conn.close()

def add_loss(user_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE players
        SET losses = losses + 1,
            mmr = mmr - 25
        WHERE user_id = %s;
    """, (user_id,))

    conn.commit()
    cursor.close()
    conn.close()

def get_ranking():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT user_id, mmr, wins, losses
        FROM players
        ORDER BY mmr DESC;
    """)

    data = cursor.fetchall()

    cursor.close()
    conn.close()

    return data
