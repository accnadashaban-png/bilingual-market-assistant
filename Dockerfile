FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MA_DB_PATH=/app/data/market.db
WORKDIR /app

COPY pyproject.toml README.md ./
COPY market_assistant ./market_assistant
COPY data/generate.py ./data/generate.py
COPY web ./web
RUN pip install --no-cache-dir -e . && python data/generate.py \
    && useradd --create-home app && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "market_assistant.api:app", "--host", "0.0.0.0", "--port", "8000"]
