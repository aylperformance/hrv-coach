FROM python:3.12-slim

# Variables d'env
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dépendances système pour scipy / numpy
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installer d'abord les deps (cache layer)
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copier le code
COPY . .

# Dossier pour la base SQLite — le volume persistant est monté par Railway
# via le dashboard sur le path /data (pas via la directive VOLUME)
RUN mkdir -p /data
ENV HRV_DB_PATH=/data/hrv_coach.db

# Le port d'écoute (Railway/Render injectent $PORT)
ENV PORT=8000
EXPOSE 8000

# uvicorn avec gestion de $PORT
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
