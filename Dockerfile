FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system dennis && adduser --system --ingroup dennis dennis

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY knowledge_base ./knowledge_base
COPY migrations ./migrations

RUN pip install --upgrade pip \
    && pip install .

RUN mkdir -p /app/data \
    && chown -R dennis:dennis /app

USER dennis

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"

CMD ["dennis-bot"]
