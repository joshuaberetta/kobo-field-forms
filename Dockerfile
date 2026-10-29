# One image for the CLI, the web interface and `kff e2e`. Fonts, the Typst renderer
# (typst-py) and the digit model are all inside: nothing is downloaded at runtime.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --system --uid 10001 --create-home kff && mkdir -p /data /app/out && chown kff /data /app/out
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 KFF_HOME=/data
WORKDIR /app
USER kff
EXPOSE 8765
CMD ["kff", "web", "--host", "0.0.0.0", "--port", "8765"]
