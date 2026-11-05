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
uv run alembic upgrade head      # create or update the database schema
```

| Task | Command |
|---|---|
| Run the API with reload | `uv run uvicorn vacancy_radar.api.app:app --reload` |
| Tests | `uv run pytest` |
| Tests with coverage | `uv run pytest --cov` |
| Lint and format check | `uv run ruff check . && uv run ruff format --check .` |
| Type check | `uv run mypy` |
| All pre-commit hooks | `uv run pre-commit run --all-files` |
| Apply migrations | `uv run alembic upgrade head` |
| Collect all sources once | `uv run python -m vacancy_radar.collect` |
| Collect one source, without writing to the database | `uv run python -m vacancy_radar.collect djinni --dry-run` |
| New migration after a model change | `uv run alembic revision --autogenerate -m "<what changed>"` |

Tests run against a real PostgreSQL database, because the project relies on FTS, GIN indexes, `pg_trgm` and `ON CONFLICT`. By default they use `vacancy_radar_test` on the Compose Postgres, create it on first run and apply the Alembic migrations to it. Each test runs inside a transaction that is rolled back afterwards. Set `TEST_DATABASE_URL` to use another server. The suite refuses to run against a database whose name does not end in `_test`.

## Collecting vacancies

`python -m vacancy_radar.collect` fetches each source once and stores every item unchanged in `raw_vacancy`. Running it again is safe: new items are inserted, items whose content changed are updated, and identical items are left alone. Each run logs one `collect.finished` line per source:

```json
{"source": "djinni", "fetched": 100, "skipped": 0, "inserted": 3, "updated": 0, "unchanged": 97, "event": "collect.finished", "level": "info", "timestamp": "..."}
```

`unchanged + updated` is the overlap with earlier runs. If it drops to 0, the feed moved on between runs and some vacancies were missed, so poll more often. Logs are JSON when stderr is not a terminal (CI, containers) and readable console output otherwise. The exit code is 1 if any source failed. See [`docs/sources.md`](docs/sources.md) for what each source allows.

## Project layout

```text
src/vacancy_radar/
├── api/        # FastAPI app and routes
├── collect/    # idempotent storage of fetched items + the collector CLI
├── db/         # SQLAlchemy models and sync/async engines
├── domain/     # pure functions: parsing, normalization, matching (no I/O)
├── sources/    # one adapter per source (fetch + parse), behind SourceAdapter
├── config.py   # settings from environment / .env
├── log.py      # structlog setup
└── worker.py   # Celery app for the worker and Beat
migrations/     # Alembic migrations (the only way the schema changes)
tests/          # pytest; fixtures/ holds saved source responses
```
