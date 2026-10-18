# Hosted Agere image. Build from the repo root:
#   docker build -f deploy/Dockerfile -t agere-hosted .
# (docker-compose.yml in this folder does that for you.)
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    AGERE_DATA_DIR=/data \
    AGERE_HOST=0.0.0.0 \
    AGERE_PORT=8080 \
    AGERE_COOKIE_SECURE=1 \
    AGERE_TERMINOLOGY_ENCODER=off

RUN groupadd --system --gid 10001 agere \
 && useradd --system --uid 10001 --gid agere --home-dir /app --shell /usr/sbin/nologin agere

WORKDIR /app

COPY deploy/requirements-hosted.txt /tmp/requirements-hosted.txt
RUN pip install --no-cache-dir -r /tmp/requirements-hosted.txt && rm /tmp/requirements-hosted.txt

# Application code only. No artifacts/, models/, tmp/, private/, .venv or databases (see /.dockerignore).
COPY clinical_core/ /app/clinical_core/
COPY config/ /app/config/
COPY serve_hosted.py /app/serve_hosted.py
COPY docs/agere/HOSTED_DESIGN.md /app/docs/agere/HOSTED_DESIGN.md

# Some modules default to <repo>/artifacts/... for ledgers and workspaces. Point that at the volume so any
# such write lands on persistent, writable storage instead of the read-only code tree.
RUN mkdir -p /data \
 && ln -s /data/artifacts /app/artifacts \
 && chown -R agere:agere /data

USER agere
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('AGERE_PORT','8080'), timeout=4); sys.exit(0 if r.status==200 else 1)"

CMD ["python", "serve_hosted.py"]
