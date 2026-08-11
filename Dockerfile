# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache
WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --requirement /tmp/requirements.lock

COPY backend ./backend
COPY --from=frontend /build/dist ./frontend/dist

RUN mkdir -p "$TIKTOKEN_CACHE_DIR" \
    && python -c "import tiktoken; [tiktoken.get_encoding(name) for name in tiktoken.list_encoding_names()]" \
    && useradd --create-home --uid 10001 estimator \
    && chown -R estimator:estimator "$TIKTOKEN_CACHE_DIR"

USER estimator
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request; port=os.getenv('PORT','8000'); urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=2)"]
CMD ["sh", "-c", "exec uvicorn token_estimator_web.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-proxy-headers"]
