# syntax=docker/dockerfile:1

# Pinned by digest so a rebuild cannot pull a different image. Base image choice: see README.
ARG PYTHON_IMAGE=python:3.13-slim-trixie@sha256:8d9d0b8bcf6506481eae4907c18f5e3e7902e629f5f6d684f9e7c32e85e3ddf0

# The installer, also pinned by digest. Only the binary is taken from it, and only into
# stages that are never deployed.
FROM ghcr.io/astral-sh/uv:0.9.15@sha256:4c1ad814fe658851f50ff95ecd6948673fffddb0d7994bdb019dcb58227abd52 AS uv

FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /bin/uv
# Install into /opt/venv with the image's own Python (never a downloaded one). Bytecode is
# compiled here because the runtime user cannot write it later.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv     UV_PYTHON_DOWNLOADS=never     UV_LINK_MODE=copy     UV_COMPILE_BYTECODE=1     UV_NO_CACHE=1
WORKDIR /build
COPY pyproject.toml uv.lock ./
# --locked: refuse to build if uv.lock no longer matches pyproject.toml, rather than quietly
# resolving something new. Every download is checked against the hash recorded in the lock.
RUN uv sync --locked --no-dev

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
# An allowlist, not "COPY . .": only what the service runs reaches the deployed image, and a
# new file in the repo cannot end up in it by accident. Stays owned by root: the app user
# can read its code but not rewrite it.
COPY manage.py gunicorn.conf.py healthcheck.py ./
COPY accounts/ accounts/
COPY config/ config/
COPY rentals/ rentals/
COPY templates/ templates/
# Settings refuse to import without these; collectstatic uses none of them. Not persisted.
RUN DJANGO_SECRET_KEY=build-only POSTGRES_DB=x POSTGRES_USER=x POSTGRES_PASSWORD=x \
    python manage.py collectstatic --noinput
# Numeric, so an orchestrator can verify "not root" without resolving a name
# (Kubernetes runAsNonRoot refuses a named user).
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "healthcheck.py"]
CMD ["gunicorn", "config.wsgi:application"]

# Never deployed. /app is read-only to the app user, so tool caches go to /tmp.
FROM runtime AS test
ENV RUFF_CACHE_DIR=/tmp/ruff-cache \
    MYPY_CACHE_DIR=/tmp/mypy-cache
USER root
COPY --from=uv /uv /bin/uv
COPY pyproject.toml uv.lock ./
# Adds the dev group to the same environment, from the same lockfile.
RUN UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_NO_CACHE=1     uv sync --locked
COPY tests/ tests/
USER 10001:10001
CMD ["pytest"]
