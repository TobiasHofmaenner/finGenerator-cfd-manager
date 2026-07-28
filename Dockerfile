# Slim application image — FastAPI + a job schema only. Postgres (CNPG) is an
# operator-managed cluster service, attached by env. No OpenFOAM, no fingen:
# this is the dispatcher, not the compute.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-install-project --frozen 2>/dev/null || uv sync --no-install-project

COPY src ./src

RUN useradd -u 10001 -m app && chown -R app /app
USER 10001

EXPOSE 8080
CMD ["uv", "run", "uvicorn", "cfdmanager.app:app", "--host", "0.0.0.0", "--port", "8080"]
