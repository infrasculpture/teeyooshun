FROM python:3.12-slim

# No .pyc files; log output straight to `docker compose logs`
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# poppler-utils: pdfinfo and pdftoppm, used to render scanned PDF pages for ingestion
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pytest.ini .
COPY docs/topics.yaml ./docs/topics.yaml
COPY app ./app
COPY tests ./tests

# Run as a non-root user (UID 1000). The data directory is bind-mounted over /app/data.
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown app:app /app/data
USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
