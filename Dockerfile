FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Kolkata

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e . \
    && useradd --create-home --shell /usr/sbin/nologin bot 2>/dev/null || true \
    && mkdir -p /app/data /app/logs \
    && chown -R bot:bot /app

USER bot
VOLUME ["/app/data", "/app/logs"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD ["python", "-c", "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"WEB_PORT\",\"8080\")}/healthz',timeout=3)"]

ENTRYPOINT ["btc-options-bot"]
CMD ["run"]
