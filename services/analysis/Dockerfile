# Build context is the repo root.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY packages/schemas /app/packages/schemas
COPY packages/bus /app/packages/bus
RUN pip install --no-cache-dir /app/packages/schemas /app/packages/bus

# Analysis reuses the ingest service's Config and TickStore. Installing it
# with --no-deps skips the Kalshi crypto stack, which this service never uses.
COPY services/ingest /app/services/ingest
RUN pip install --no-cache-dir --no-deps /app/services/ingest \
    && pip install --no-cache-dir asyncpg

COPY services/analysis /app/services/analysis
RUN pip install --no-cache-dir /app/services/analysis

RUN useradd --create-home --uid 10001 kalshi
USER kalshi

CMD ["kalshidive-analysis"]
