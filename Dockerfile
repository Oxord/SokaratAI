FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements-web.txt .
RUN pip install -r requirements-web.txt

COPY sokrat ./sokrat
COPY web ./web
COPY public ./public
COPY data/questions.json ./data/questions.json

# Непривилегированный пользователь; каталог данных — для сессий и пользователей.
RUN useradd -m app && mkdir -p /app/data/sessions && chown -R app /app
USER app

ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn web.server:app --host 0.0.0.0 --port ${PORT}"]
