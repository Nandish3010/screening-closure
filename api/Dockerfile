# Two stages. `runtime` is what runs in compose: production dependencies only, no tests, no dev extras (pytest,
# hypothesis, httpx), and an unprivileged user (SEC-1). `test` adds the dev extras and the tests on top of it for
# `docker compose --profile tests run --rm --no-deps api-test`, the fallback of `make test` without a local .venv.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
RUN pip install --no-cache-dir .

USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM runtime AS test
USER root
COPY tests ./tests
RUN pip install --no-cache-dir ".[dev]"
USER app
CMD ["pytest", "tests", "-q"]
