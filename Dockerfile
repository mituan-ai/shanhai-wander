FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && groupadd --gid 10001 wander \
    && useradd --uid 10001 --gid wander --no-create-home --shell /usr/sbin/nologin wander
COPY . .
RUN chmod -R a+rX /app \
    && mkdir -p /app/data /app/staticfiles \
    && chown -R 10001:10001 /app/data /app/staticfiles \
    && chmod 700 /app/data \
    && chmod +x /app/scripts/start.sh
USER 10001:10001
VOLUME ["/app/data"]
EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["./scripts/start.sh"]
