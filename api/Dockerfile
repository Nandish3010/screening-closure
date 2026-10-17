FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY tests ./tests

# Dev extras (pytest, hypothesis) are installed on purpose so `make test` and the
# live property-test demo work offline inside the container.
RUN pip install --no-cache-dir ".[dev]"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
