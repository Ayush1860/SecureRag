# syntax=docker/dockerfile:1.7
# Multi-stage build: frontend bundle -> Python deps + cached embedding model -> slim non-root runtime.

# --- 1. React frontend -------------------------------------------------------------------
FROM node:20-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. Python dependencies and model cache -------------------------------------------------
FROM python:3.11-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 USE_TF=0 HF_HOME=/opt/hf
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
# CPU-only torch keeps the image ~1.5 GB smaller; installed first so sentence-transformers reuses it.
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.2,<3"
COPY requirements.txt .
RUN pip install -r requirements.txt
# Bake the embedding model into the image so the container starts (and runs) offline.
ARG EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2
RUN python -c "from sentence_transformers import SentenceTransformer as S; S('${EMBED_MODEL}')"

# --- 3. Runtime --------------------------------------------------------------------------
FROM python:3.11-slim AS runtime
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
 && mkdir -p /data && chown app:app /data
ENV PATH=/opt/venv/bin:$PATH \
    HF_HOME=/opt/hf HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 USE_TF=0 \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    ENV=prod AUTH_MODE=api_key LOG_FORMAT=json \
    DATA_DIR=/app/data/sample \
    CHROMA_DIR=/data/chroma_db \
    STATE_DB_PATH=/data/ingest_state.sqlite \
    SPARSE_DIR=/data/sparse \
    AUDIT_LOG_PATH=/data/audit/audit.jsonl \
    API_KEYS_FILE=/data/api_keys.json
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/hf /opt/hf
WORKDIR /app
COPY --chown=app:app securerag ./securerag
COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app data/sample ./data/sample
COPY --chown=app:app --from=frontend /fe/dist ./frontend/dist
USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/api/health').status != 200)"
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
