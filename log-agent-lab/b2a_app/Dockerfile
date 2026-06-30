FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --system .
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8003"]
