# Центр multisite: сборка React-фронта + FastAPI (WSS-реестр агентов + REST + UI).
# Контекст сборки — корень репо (нужен web/ и center/).
#
#   docker build -f center/Dockerfile -t mdv-center .

# ── Этап 1: сборка фронта (Vite/React) ───────────────────────────────
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm install
COPY web/ ./
RUN npm run build           # результат: /web/dist

# ── Этап 2: рантайм (FastAPI + websockets, БЕЗ msmart — центр в LAN не ходит) ──
FROM python:3.12-slim
WORKDIR /app

COPY center/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY center/server.py /app/
COPY --from=web /web/dist /app/static

ENV AC_STATIC_DIR=/app/static
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
