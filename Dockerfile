# ── Этап 1: сборка фронта (Vite/React) ───────────────────────────────
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm install
COPY web/ ./
RUN npm run build           # результат: /web/dist

# ── Этап 2: рантайм (FastAPI + msmart) ───────────────────────────────
FROM python:3.12-slim
WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/
COPY --from=web /web/dist /app/static

ENV AC_STATIC_DIR=/app/static \
    AC_DEVICES_FILE=/app/devices.json

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
