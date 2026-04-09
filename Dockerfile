# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency manifests
COPY pyproject.toml uv.lock* ./

# Install all dependencies into a virtual environment under /build/.venv
RUN uv sync --no-install-project --no-dev

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user for security
RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app

# Copy the virtual environment from builder
COPY --from=builder /build/.venv /app/.venv

# Copy application source
COPY app/ ./app/

# Create data directory for SQLite persistent disk (Render mounts /data)
RUN mkdir -p /data && chown app:app /data

USER app

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV DATABASE_URL="sqlite+aiosqlite:////data/monitor.db"

EXPOSE 10000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]
