# Build context is the repo root -- the service depends on packages/ and would
# otherwise be unable to see them.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Local packages first: they change far less often than service code, so this
# layer stays cached across most rebuilds.
COPY packages/schemas /app/packages/schemas
COPY packages/bus /app/packages/bus
RUN pip install --no-cache-dir /app/packages/schemas /app/packages/bus

COPY services/ingest /app/services/ingest
RUN pip install --no-cache-dir /app/services/ingest

# Non-root: this process holds Kalshi credentials and a database connection,
# and nothing it does requires root.
RUN useradd --create-home --uid 10001 kalshi
USER kalshi

CMD ["kalshidive-ingest"]
