FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
# Templates are inside src/gktrader/templates — already copied above

RUN pip install --no-cache-dir . && \
    playwright install chromium --with-deps

CMD ["uvicorn", "gktrader.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
