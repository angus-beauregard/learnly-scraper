# Multi-stage build targeting linux/arm64 (Raspberry Pi / CasaOS)
FROM python:3.11-slim-bookworm

# Explicit platform label so docker buildx picks the right variant
ARG TARGETPLATFORM=linux/arm64

WORKDIR /app

# Install OS-level build deps needed by some Python packages on ARM64
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scraper.py .

# The .env file is mounted at runtime via Docker secrets or --env-file;
# never bake credentials into the image.
ENV PYTHONUNBUFFERED=1

# Output CSV lives in a named volume so data persists across container restarts.
VOLUME ["/app/data"]
ENV OUTPUT_CSV=/app/data/rscore_data.csv

CMD ["python", "scraper.py"]
