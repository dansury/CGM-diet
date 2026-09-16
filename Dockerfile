# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY pyproject.toml README.md ./
COPY src ./src
# `pdf` extra: local text extraction from lab PDFs, and rendering the pages of
# a scan for the vision path (`spec/ingest.md` § PDF).
# `barcode` extra: EAN off a pack photo before the model is asked
# (`spec/ingest.md` § Штрихкод); the runtime stage carries its `libzbar0`.
RUN pip install ".[pdf,barcode]"


FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    APP_HOME=/app \
    MPLCONFIGDIR=/tmp/matplotlib

# libzbar0 — the shared library `pyzbar` binds to; without it the barcode
# path stays off and the label flow works exactly as before.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini libzbar0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown -R app:app /app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app src ./src
COPY --chown=app:app seeds ./seeds
COPY --chown=app:app scripts ./scripts

USER app
VOLUME ["/app/data"]
EXPOSE 8080

ENTRYPOINT ["tini", "--"]
CMD ["/app/scripts/entrypoint.sh"]
