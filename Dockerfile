# ==============================================================================
# Multi-Stage Production Dockerfile for LearnMesh Platform
# ==============================================================================

# Stage 1: Build stage
FROM python:3.12-slim as builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --user -r requirements.txt

# Stage 2: Final runtime stage
FROM python:3.12-slim as runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/learnmesh/.local/bin:$PATH" \
    PYTHONPATH=/app

# Create non-root system user
RUN groupadd -g 1001 learnmesh && \
    useradd -u 1001 -g learnmesh -m -s /bin/bash learnmesh

# Copy installed wheels from builder
COPY --from=builder /root/.local /home/learnmesh/.local

# Copy application code
COPY backend /app/backend
COPY migrations /app/migrations
COPY alembic.ini /app/alembic.ini
COPY frontend /app/frontend
COPY .env.example /app/.env.example

RUN chown -R learnmesh:learnmesh /app /home/learnmesh/.local

USER learnmesh

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
