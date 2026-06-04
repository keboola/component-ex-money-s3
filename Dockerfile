FROM python:3.13-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /code/

COPY pyproject.toml .
COPY uv.lock .

ENV UV_PROJECT_ENVIRONMENT="/usr/local/"
RUN uv sync --all-groups --frozen

COPY src/ src
COPY tests/ tests
COPY scripts/ scripts


FROM base AS test
RUN uv run ruff check .
CMD ["python", "-m", "pytest", "-v", "tests"]


FROM base AS production
CMD ["python", "-u", "src/component.py"]
