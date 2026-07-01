# Securities QA Agent — Dockerfile
# Multi-stage build: builder (install deps) → runtime (slim image)

# ─── Stage 1: Builder ────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Copy source and install project
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir --prefix=/install .

# ─── Stage 2: Runtime ────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Install runtime utilities (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Security: run as non-root user
RUN groupadd -r qa && useradd -r -g qa -G qa qa

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source code
COPY src/ src/
COPY frontend/ frontend/
COPY config.example.yaml .

# Create data directory with correct permissions
RUN mkdir -p /app/data && chown -R qa:qa /app/data

# Switch to non-root user
USER qa

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

# Expose port
EXPOSE 8001

# Health check (using curl for reliability in slim images)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8001/api/v1/qa/health || exit 1

# Run the application
CMD ["uvicorn", "qa.api.server:app", "--host", "0.0.0.0", "--port", "8001"]
