# =============================================================================
# Multi-stage Dockerfile
#
# Primary target : linux/arm/v7  (Raspberry Pi 3, 32-bit Raspberry Pi OS)
# Also builds on : linux/arm64   (Pi 3/4 with 64-bit OS)
#                  linux/amd64   (dev / CI)
#
# Architecture notes
# ------------------
# Playwright Chromium ships binaries for amd64 and arm64 only.
# On arm/v7 (32-bit) the browser-based scrapers are unavailable; the FastAPI
# receiver and the requests-based reddit_public.py work on all architectures.
#
# Build for a specific platform:
#   docker buildx build --platform linux/arm/v7  -t learnly:armv7  .
#   docker buildx build --platform linux/arm64   -t learnly:arm64  .
# =============================================================================

# ---- Stage 1: build deps in an isolated layer --------------------------------
FROM python:3.11-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .

RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --upgrade pip \
    && /venv/bin/pip install --no-cache-dir -r requirements.txt

# ---- Stage 2: lean runtime image ---------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

# Install Playwright Chromium only on architectures that support it.
# TARGETARCH is set automatically by `docker buildx build --platform …`.
#   arm/v7 (32-bit Pi OS) → skip  — no Chromium binary available
#   arm64  (64-bit Pi OS) → install
#   amd64  (dev/CI)       → install
ARG TARGETARCH
RUN if [ "${TARGETARCH}" = "arm64" ] || [ "${TARGETARCH}" = "amd64" ]; then \
        playwright install --with-deps chromium; \
    else \
        echo "Playwright Chromium skipped on ${TARGETARCH:-arm} (not supported on 32-bit ARM)."; \
        echo "Browser scraping unavailable; use reddit_public.py + the API receiver instead."; \
    fi

# ---- Application -------------------------------------------------------------
WORKDIR /app
COPY . .

RUN mkdir -p /app/data
VOLUME ["/app/data"]

ENV PYTHONUNBUFFERED=1
ENV DB_PATH=/app/data/harvest.db

EXPOSE 8000

# Default: run the FastAPI ingest receiver.
# Override at runtime for the Reddit scraper:
#   docker run … python -m extractors.reddit_public
CMD ["uvicorn", "api.receiver:app", "--host", "0.0.0.0", "--port", "8000"]
