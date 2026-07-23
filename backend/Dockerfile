FROM mwader/static-ffmpeg:7.1.1-amd64@sha256:6769881cc02c80d33e387750a8e144d162adfab2775e934dd97899261dda3a0c AS ffmpeg

FROM python:3.12-alpine3.23@sha256:601d3d3797e90e2534782e69c85fafb7971b43f24c7b1b079b7e48dd435e458d AS runtime

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app app

COPY pyproject.toml ./pyproject.toml
COPY app ./app
COPY mcp_servers ./mcp_servers
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --index-url "$PIP_INDEX_URL" --timeout 120 --retries 3 \
    "pip==26.1.2" ".[stage5]"
COPY --from=ffmpeg /ffprobe /usr/local/bin/ffprobe

USER app
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
