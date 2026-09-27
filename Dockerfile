# syntax=docker/dockerfile:1.7
# Multi-stage build.
#   runtime (default, last stage): the non-root API image used by docker-compose.
#   lambda  (--target lambda):     the same app behind AWS Lambda Web Adapter, with an encrypted
#                                  demo index baked in (needs the build secret "aes_key").

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

# --- 3. Application base (shared by runtime, lambda and the demo-index build) --------------------
FROM python:3.11-slim AS app-base
ENV PATH=/opt/venv/bin:$PATH \
    HF_HOME=/opt/hf HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 USE_TF=0 ANONYMIZED_TELEMETRY=False \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    ENV=prod AUTH_MODE=api_key LOG_FORMAT=json
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/hf /opt/hf
# Plain-directory alias of the baked model. The Lambda image (read-only filesystem) and the demo index
# it ships load the model from this path, so no Hugging Face cache lookups or cache writes happen.
RUN mkdir -p /opt/models \
 && ln -s "$(ls -d /opt/hf/hub/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/*)" /opt/models/embed \
 && test -f /opt/models/embed/modules.json
WORKDIR /app
COPY securerag ./securerag
COPY app ./app
COPY scripts ./scripts
COPY data/sample ./data/sample
COPY --from=frontend /fe/dist ./frontend/dist

# --- 4. Encrypted demo index for Lambda (build-time ingest) ------------------------------------
# The AES key comes from a BuildKit secret mount: it exists only while this RUN executes and is
# never written to a layer. Only ciphertext, vectors and hashed terms leave this stage; the
# plaintext demo corpus stays behind.
FROM app-base AS demo-index
ENV EMBED_MODEL=/opt/models/embed
ARG DEMO_SYNTHETIC_DOCS=1000
ARG DEMO_SEED=7
RUN python scripts/generate_corpus.py --docs ${DEMO_SYNTHETIC_DOCS} --seed ${DEMO_SEED} --out /build/corpus \
 && cp -r data/sample/. /build/corpus/
RUN mkdir -p /opt/demo/index
RUN --mount=type=secret,id=aes_key,required=true \
    SECURERAG_AES_KEY_B64="$(cat /run/secrets/aes_key)" \
    DATA_DIR=/build/corpus CHROMA_DIR=/opt/demo/index/chroma \
    STATE_DB_PATH=/opt/demo/index/ingest_state.sqlite SPARSE_DIR=/opt/demo/index/sparse \
    python scripts/ingest.py --json > /opt/demo/ingest_report.json \
 && python -c "import json; r=json.load(open('/opt/demo/ingest_report.json')); assert r['rejected']==0 and r['failed']==0, r; print(r['chunks_written'], 'chunks')"

# --- 5. AWS Lambda image (docker build --target lambda --secret id=aes_key,src=...) --------
FROM app-base AS lambda
# Lambda Web Adapter runs the unchanged uvicorn app as a Lambda extension.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4 /lambda-adapter /opt/extensions/lambda-adapter
COPY --from=demo-index /opt/demo/index /opt/demo/index
ENV AWS_LWA_PORT=8000 \
    AWS_LWA_READINESS_CHECK_PATH=/api/health \
    AWS_LWA_ASYNC_INIT=true \
    LAMBDA_MODE=1 \
    DEMO_INDEX_DIR=/opt/demo/index \
    EMBED_MODEL=/opt/models/embed \
    HOME=/tmp XDG_CACHE_HOME=/tmp/.cache MPLCONFIGDIR=/tmp/.mpl \
    LLM_FALLBACKS=refusal RERANK_ENABLED=false
# Lambda runs the image as an unprivileged user with a read-only filesystem apart from /tmp;
# everything under /app, /opt stays world-readable (default modes).
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# --- 6. Runtime (default target; docker-compose) -----------------------------------------------
FROM app-base AS runtime
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
 && mkdir -p /data && chown app:app /data
ENV DATA_DIR=/app/data/sample \
    CHROMA_DIR=/data/chroma_db \
    STATE_DB_PATH=/data/ingest_state.sqlite \
    SPARSE_DIR=/data/sparse \
    AUDIT_LOG_PATH=/data/audit/audit.jsonl \
    API_KEYS_FILE=/data/api_keys.json
USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/api/health').status != 200)"
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
