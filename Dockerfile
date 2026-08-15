# Multi-stage. Fixes carried forward from the two predecessor images, which
# shipped tests/ into production, ran as root, had no healthcheck, and carried
# build-essential.
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd -m -u 10001 app
WORKDIR /app

FROM base AS deps
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e ".[test]"

FROM deps AS runtime
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=25s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["sh", "-c", "uvicorn ticket_reconciler.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]

FROM deps AS test
COPY . .
RUN pip install --no-cache-dir -e ".[test]"
# COPY lands as root but the suite runs as `app`, and coverage writes its data
# file into the working directory.
RUN chown -R app:app /app
USER app
CMD ["./run_tests.sh"]
