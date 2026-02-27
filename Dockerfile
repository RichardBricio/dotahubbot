FROM python:3.11-slim

# Instala dependências de sistema para compilar pacotes Python e PostgreSQL
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Instala as dependências do Python
RUN pip install --no-cache-dir -r requirements.txt

# Comando para iniciar o bot principal
CMD ["python", "bot2.py"]