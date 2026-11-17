FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    MCP_TRANSPORT=http \
    MCP_STATELESS_HTTP=true \
    NOLGA_BACKEND_URL=https://nolga-backend-develop.onrender.com \
    NOLGA_API_KEYS_FILE=/data/api_keys.json

WORKDIR /app

RUN pip install "fastmcp==4.0.2" "pydantic>=2.0"

COPY server.py ./

RUN useradd --create-home --uid 1000 nolga \
    && mkdir -p /data \
    && chown -R nolga:nolga /data /app

USER nolga

VOLUME ["/data"]
EXPOSE 8080

CMD ["python", "server.py"]
