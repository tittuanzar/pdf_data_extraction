FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build tools are kept minimal; the PDF stack works with pure Python wheels in most cases.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gcc \
       build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.2 /uv /bin/uv

COPY pyproject.toml README.md schema.json ./
COPY src ./src

RUN uv sync

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "poc_valves.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
