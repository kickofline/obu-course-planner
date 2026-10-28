FROM ghcr.io/astral-sh/uv:0.10.2 AS uv

# Node 22 (>= 22.12) builds the Astro frontend. Astro's outDir is ../src/obu_course_planner/static,
# relative to frontend/, so the built site lands in this stage's /app/src tree.
FROM node:22-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock LICENSE ./
COPY src ./src
COPY --from=frontend /app/src/obu_course_planner/static ./src/obu_course_planner/static
# --no-editable bakes the package (including the generated static/ build) into the wheel in .venv.
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app
COPY --from=build /app/.venv /app/.venv
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).read()"]
CMD ["python", "-m", "obu_course_planner.server", "--host", "0.0.0.0", "--port", "8000"]