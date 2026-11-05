# Vacancy Radar

IT job aggregator with market analytics. It collects vacancies from public feeds, deduplicates and normalizes them, sends subscription matches to Telegram, and shows market analytics.

Status: early development. See [`PLAN.md`](PLAN.md) for the roadmap and [`docs/sources.md`](docs/sources.md) for how each data source may be used.

## Quickstart

Requires Docker with Compose v2.

```sh
docker compose up --build
```

This starts the API, a Celery worker, Celery Beat, PostgreSQL 16 and Redis. Then check:

```sh
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}
```

To override settings, copy `.env.example` to `.env` and edit it.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```sh
uv sync                          # create .venv with all dependencies
uv run pre-commit install        # run linters on every commit
cp .env.example .env             # settings for running on the host
docker compose up -d postgres redis
```

| Task | Command |
|---|---|
| Run the API with reload | `uv run uvicorn vacancy_radar.api.app:app --reload` |
| Tests | `uv run pytest` |
| Tests with coverage | `uv run pytest --cov` |
| Lint and format check | `uv run ruff check . && uv run ruff format --check .` |
| Type check | `uv run mypy` |
| All pre-commit hooks | `uv run pre-commit run --all-files` |

Tests run against a real PostgreSQL database, because the project relies on FTS, GIN indexes, `pg_trgm` and `ON CONFLICT`. By default they use `vacancy_radar_test` on the Compose Postgres and create it on first run. Set `TEST_DATABASE_URL` to use another server. The suite refuses to run against a database whose name does not end in `_test`.

## Project layout

```text
src/vacancy_radar/
├── api/        # FastAPI app and routes
├── domain/     # pure functions: parsing, normalization, matching (no I/O)
├── config.py   # settings from environment / .env
└── worker.py   # Celery app for the worker and Beat
tests/          # pytest; fixtures/ holds saved source responses
```
