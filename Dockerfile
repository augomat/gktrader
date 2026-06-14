FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml README.md ./

RUN python - <<'PY' > /tmp/requirements.txt
import pathlib
import tomllib

project = tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]
for dependency in project["dependencies"]:
    print(dependency)
PY

RUN pip install -r /tmp/requirements.txt && \
    playwright install chromium --with-deps

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
# Templates are inside src/gktrader/templates — already copied above

RUN pip install --no-deps -e .

CMD ["uvicorn", "gktrader.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
