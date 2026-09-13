# Use official uv image with Python 3.14 for fast, reproducible builds
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PYTHONUNBUFFERED=1

# Copy dependency definitions first for optimal layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies without installing project itself
RUN uv sync --frozen --no-install-project --no-dev

# Copy application code, migrations, and configuration
COPY agentic_trader/ ./agentic_trader/
COPY config/ ./config/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY README.md ./

# Sync project package
RUN uv sync --frozen --no-dev

# Place executables in PATH
ENV PATH="/app/.venv/bin:$PATH"

# Ensure data directory exists for SQLite database
RUN mkdir -p /app/data

# Expose Prometheus metrics and health check port
EXPOSE 9108

# Default to daemon mode (runs 4h scheduler + Telegram listener + Prometheus exporter)
CMD ["copilot", "daemon"]
