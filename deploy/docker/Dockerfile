FROM node:22-bookworm-slim AS admin-build
WORKDIR /build
COPY admin/package*.json ./
RUN npm ci
COPY admin/ ./
RUN VITE_API_BASE_URL= VITE_DEMO_MODE=false npm run build

FROM python:3.13-slim-bookworm AS python-build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY backend/requirements.txt ./
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.13-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home app
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    TMPDIR=/app/data/tmp HF_HOME=/app/data/model-cache HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    EMBEDDINGS_MODEL_PATH=/models/embedding RERANKER_MODEL_PATH=/models/reranker \
    EMBEDDINGS_AUTO_DOWNLOAD=false RERANKER_AUTO_DOWNLOAD=false \
    ADMIN_FRONTEND_DIR=/app/admin-dist FILE_PROCESSING_WORKERS=2 TOKENIZERS_PARALLELISM=false ANONYMIZED_TELEMETRY=false
WORKDIR /app
COPY --from=python-build /opt/venv /opt/venv
COPY backend/config.py backend/server.py ./
COPY backend/src ./src
COPY backend/scripts ./scripts
COPY backend/deploy/docker ./deploy/docker
COPY --from=admin-build /build/dist ./admin-dist
RUN mkdir -p data/tmp && chown -R 10001:10001 data && /opt/venv/bin/pip freeze > /app/dependencies.txt
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --start-period=300s --interval=30s --timeout=5s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).read()"]
ENTRYPOINT ["python", "/app/deploy/docker/offline.py"]
CMD ["serve"]

FROM runtime AS test
ENV EMBEDDINGS_MODEL_PATH=/app/data/embeddings/granite-embedding-97m-multilingual-r2 PYTEST_ADDOPTS="-o cache_dir=/app/data/tmp/pytest-cache"
USER root
COPY backend/requirements-dev.txt /tmp/requirements-dev.txt
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt
COPY backend/tests ./tests
USER 10001:10001
ENTRYPOINT ["python", "-m", "pytest"]
CMD ["-q"]
