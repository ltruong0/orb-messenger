# msg-hub image. Build from the repo root so contracts/ is in the context:
#   docker build -f hub/Dockerfile -t ghcr.io/ltruong0/orb-messenger/msg-hub:dev .
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
# Noto Color Emoji for sticker rendering (docs/hub-api.md "Stickers")
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*
# pip rather than COPY --from=ghcr.io/astral-sh/uv: the legacy builder can't
# cross-platform a COPY --from image, and this image is built on an arm64 Mac for an amd64 node
RUN pip install --no-cache-dir 'uv>=0.10,<0.11'

WORKDIR /srv
# workspace manifests first so dependency layers cache across code changes
COPY pyproject.toml uv.lock ./
COPY hub/pyproject.toml hub/pyproject.toml
COPY tools/orb-sim/pyproject.toml tools/orb-sim/pyproject.toml
RUN uv sync --frozen --no-dev --package msg-hub --no-install-workspace

COPY contracts/schemas contracts/schemas
COPY hub hub
RUN uv sync --frozen --no-dev --package msg-hub

ENV PATH="/srv/.venv/bin:$PATH" \
    DATA_DIR=/data \
    CONTRACTS_DIR=/srv/contracts/schemas \
    USERS_FILE=/srv/hub/users.yaml \
    LISTEN_PORT=8080
RUN useradd -r -u 1000 -d /srv hub && mkdir -p /data && chown hub:hub /data
USER hub
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["msg-hub"]
