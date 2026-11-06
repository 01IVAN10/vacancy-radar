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

Tests run against a real PostgreSQL database, because the project relies on FTS, GIN indexes, `pg_trgm` and `ON CONFLICT`. By default they use `vacancy_radar_test` on the Compose Postgres, create it on first run and apply the Alembic migrations to it. Each test runs inside a transaction that is rolled back afterwards. Set `TEST_DATABASE_URL` to use another server. The suite refuses to run against a database whose name does not end in `_test`. Tests that need Redis use database 15 on the Compose Redis and empty it around each test. Set `TEST_REDIS_URL` to use another one, but never the broker database of a running worker.

## Collecting vacancies

`python -m vacancy_radar.collect` fetches each source once and stores every item unchanged in `raw_vacancy`. Running it again is safe: new items are inserted, items whose content changed are updated, and identical items are left alone. Each run logs one `collect.finished` line per source:

```json
{"source": "djinni", "fetched": 100, "skipped": 0, "inserted": 3, "updated": 0, "unchanged": 97, "event": "collect.finished", "level": "info", "timestamp": "..."}
```

`unchanged + updated` is the overlap with earlier runs. If it drops to 0, the feed moved on between runs and some vacancies were missed, so poll more often. Logs are JSON when stderr is not a terminal (CI, containers) and readable console output otherwise. The exit code is 1 if any source failed. See [`docs/sources.md`](docs/sources.md) for what each source allows.

### Scheduled collection

Until the VPS exists, the [`Collect`](.github/workflows/collect.yml) GitHub Actions workflow runs every 15 minutes, started by an external cron (see [Trigger](#trigger)). It applies pending migrations and then runs the collector against a free [Neon](https://neon.com) Postgres 16 database. The connection URL is the `DATABASE_URL` repository secret, in the form `postgresql+psycopg://<role>:<password>@<host>/<database>?sslmode=require&channel_binding=require`. This is the direct (not `-pooler`) connection string from the Neon console, with the scheme changed to `postgresql+psycopg://`. The role and database names are the ones the console shows. For running commands against Neon locally, keep the same line in a gitignored `.env.neon` and pass `uv run --env-file .env.neon …`. The job needs no Redis. A failing source turns the run red.

Each source sets a `min_interval`: Djinni 25 minutes, DOU 30 minutes, the limit in DOU's consent. A source is skipped until that much time has passed since its last request, as recorded in `collect_run`. So cron delays never make a request too early. To stop a source without a code change, set the `DISABLED_SOURCES` repository variable, e.g. to `dou` (Settings → Secrets and variables → Actions → Variables).

#### Trigger

GitHub's own `schedule` trigger drops most runs of this workflow: on the first night it fired 2 times in ~14 hours, and DOU's feed moved past unseen vacancies during the 7-hour gap ([measurements](docs/collection.md#triggers)). So [cron-job.org](https://cron-job.org) starts the workflow through the API instead, and `schedule` stays only as a backup. Both triggers can fire in the same window. The `collect` concurrency group runs them one at a time, and `min_interval` makes the later one skip the sources it would fetch too early.

To set the trigger up again:

1. Create a fine-grained personal access token (Settings → Developer settings → Fine-grained tokens) for this repository only, with the single repository permission **Actions: Read and write**, expiring in 90 days. If the token leaks, it can only start, cancel, or delete workflow runs and enable or disable workflows. It cannot read secrets or push code.
2. Create a cron-job.org job:

   | Field | Value |
   |---|---|
   | URL | `https://api.github.com/repos/01IVAN10/vacancy-radar/actions/workflows/collect.yml/dispatches` |
   | Schedule | minutes `7,22,37,52` of every hour, UTC (between the backup `schedule` minutes) |
   | Method | `POST` |
   | Headers | `Accept: application/vnd.github+json`, `Authorization: Bearer <token>`, `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json` |
   | Body | `{"ref":"main"}` |
   | Notifications | on failure |

3. A test run returns `204`, and a new `workflow_dispatch` run appears in `gh run list --workflow collect.yml`.

When the token expires, the API returns `401` and cron-job.org sends a failure notification. To fix it, create a new token and replace the `Authorization` header. GitHub still disables a workflow with a `schedule` after 60 days without commits to the repository, and a disabled workflow rejects dispatches too. If the cron-job.org job starts failing for that reason, run `gh workflow enable collect.yml`.

| Task | Command |
|---|---|
| Run a collection now | `gh workflow run collect.yml` |
| Recent runs | `gh run list --workflow collect.yml` |

Every attempt is also recorded in the `collect_run` table. [`docs/collection.md`](docs/collection.md) has the SQL that checks runs per day, gaps between runs, runs that may have missed items, and failures.

Back up the hosted database to a local file. `pg_dump` needs a plain `postgresql://` URL, so `+psycopg` is stripped. With Docker, no local Postgres client is needed:

```sh
mkdir -p ~/Backups/vacancy-radar
docker run --rm -e PGURL="$(sed -n 's/^DATABASE_URL=//p' .env.neon | sed 's/+psycopg//')" \
  postgres:16 sh -c 'pg_dump --format=custom "$PGURL"' \
  > ~/Backups/vacancy-radar/vacancy_radar-$(date +%F).dump
```

With a local client instead (`brew install libpq`, version 16 or newer), run `pg_dump --format=custom --file=<file>.dump "<URL without +psycopg>"`.

The data moves to the VPS at deploy time (stage 11 in [`PLAN.md`](PLAN.md)).

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
