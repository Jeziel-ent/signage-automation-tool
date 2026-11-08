# The Signage website (API + the built web app) for a Linux server. CorelDRAW is NOT in here: with SIGNAGE_ENGINE=remote every conversion,
# scene build and export is a job for a designer's Windows agent (see docs/deploy-droplet.md).
#
#   docker build -t signage .
#   docker compose up -d --build        (with deploy/server.env filled in; see docker-compose.yml)

# ---- 1. the web app
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx vite build

# ---- 2. the server
FROM python:3.10-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    SIGNAGE_ENGINE=remote SIGNAGE_REQUIRE_REAL_ENGINE=1 \
    SIGNAGE_DATA=/data SIGNAGE_FRONTEND_DIST=/app/web SIGNAGE_AGENT_DIST=/agent_dist
WORKDIR /app/backend
COPY backend/requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt
COPY backend/ ./
COPY --from=web /web/dist /app/web
RUN useradd --system --uid 10001 --home-dir /app signage && mkdir -p /data /agent_dist && chown -R signage /data
USER signage
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
# behind Caddy (docker-compose.yml): trust its X-Forwarded-* headers so cookies and download links use https and the real client address
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
