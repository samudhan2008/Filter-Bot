FROM python:3.10-slim

# Fonts for the poster text fallback (utils/poster.py looks for DejaVu/Liberation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    fonts-liberation \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir -r requirements.txt

COPY . .

# Persisted poster cache dir (also settable via POSTER_CACHE_DIR env var)
RUN mkdir -p poster_cache

EXPOSE 8080

CMD ["python3", "bot.py"]
