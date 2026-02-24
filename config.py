import os

def get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Variável de ambiente obrigatória não definida: {name}")
    return value


TOKEN = get_env("TOKEN")
GUILD_ID = int(get_env("GUILD_ID"))

DATABASE_URL = get_env("DATABASE_URL")

# Garante sslmode=require na URL
if "sslmode=" not in DATABASE_URL:
    separator = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL = f"{DATABASE_URL}{separator}sslmode=require"
