FROM python:3.11-slim

WORKDIR /app

# Install uv for fast dependency management
RUN pip install uv

# Copy dependency files
COPY pyproject.toml ./

# Install dependencies using uv (no SQLite dependency in production)
RUN uv sync --frozen --no-cache --all-extras

# Copy application code
COPY . .

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8089

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8089/health')" || exit 1

# Run with uvicorn (server:app refers to server.py)
CMD ["uv", "run", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8089"]
