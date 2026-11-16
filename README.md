# Vacancy Radar

[![CI](https://github.com/01IVAN10/vacancy-radar/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/01IVAN10/vacancy-radar/actions/workflows/ci.yml?query=branch%3Amain)
[![Coverage](https://codecov.io/gh/01IVAN10/vacancy-radar/branch/main/graph/badge.svg)](https://codecov.io/gh/01IVAN10/vacancy-radar)

IT job aggregator with market analytics. It collects vacancies from public feeds, deduplicates and normalizes them, sends subscription matches to Telegram, and shows market analytics.

Status: stages 0–10 of [`PLAN.md`](PLAN.md) are done: collection from Djinni and DOU, normalization, accounts and subscriptions, Telegram delivery, deduplication across sources, search and analytics. Collection runs on a schedule into a free hosted database, and the whole stack runs locally with one command. The production deploy is deferred (see [Deployment](#deployment-deferred)). [`docs/sources.md`](docs/sources.md) describes how each data source may be used.

## Screenshots

Real data from Djinni and DOU, 5–15 November 2026 ([Real data for a demo](#real-data-for-a-demo)).

| Search | Top skills |
|---|---|
| ![Search for "python" at /vacancies: jobs with level, format, skills and an excerpt](docs/images/vacancies.png) | ![Top skills of the month at /analytics, with the sample size](docs/images/analytics.png) |
| **Salaries by week** | **Subscriptions** |
| ![Weekly salary median and 25th–75th percentiles, with n per week](docs/images/analytics-salaries.png) | ![Subscriptions at /subscriptions](docs/images/subscriptions.png) |

Matches arrive in Telegram, one message per job, with the subscription that matched:

<img src="docs/images/telegram.png" alt="Three matches in Telegram: title, format, skills, a link to the source and the matching subscription" width="420">

### Demo

https://github.com/user-attachments/assets/acb0b88a-7700-4330-ac67-53b2089cffc4

## Architecture

Two collectors share one adapter per source and the same storage code. Today a GitHub Actions job, started by an external cron, collects into a free hosted Postgres, because RSS feeds keep no history. Locally, `docker compose up` runs the full stack, where Celery collects and processes vacancies.

```mermaid
flowchart TB
    feeds["Djinni and DOU<br/>RSS feeds"]

    subgraph hosted["Hosted today (free tiers)"]
        cron["cron-job.org<br/>every 15 min"] --> gha["GitHub Actions<br/>Collect workflow"]
        gha --> neon[("Neon Postgres<br/>raw items")]
    end

    subgraph local["docker compose up"]
        beat["Celery Beat"] --> redis[("Redis<br/>broker, rate limit")]
        redis --> worker["Celery worker"]
        worker --> pg[("PostgreSQL 16")]
        api["FastAPI + HTMX<br/>search, analytics, accounts"] --> pg
        bot["Telegram bot<br/>long polling"] --> pg
    end

    tg["Telegram Bot API"]

    feeds --> gha
    feeds --> worker
    neon -. "pg_dump backup,<br/>demo data" .-> pg
    worker -- "matches" --> tg
    bot <--> tg
```

Inside the database every step derives its table from the one before, so a better parser rebuilds everything from the raw items:

```mermaid
flowchart LR
    raw["raw_vacancy<br/>items as fetched"] -- "normalize.pending<br/>domain parsers" --> vacancy["vacancy<br/>salary, level, format, skills"]
    vacancy -- "notify.match<br/>same-job rule" --> cluster["vacancy_cluster<br/>one per job"]
    cluster -- "subscriptions" --> delivery["notification, delivery<br/>one per account and job"]
    delivery -- "notify.send" --> telegram["Telegram"]
    vacancy -- "full-text search" --> search["/vacancies"]
    cluster -- "analytics.refresh<br/>materialized views" --> analytics["/analytics"]
```

## Decisions and trade-offs

- **Raw first.** Every fetched item is stored unchanged, and normalized rows are derived from it, so a parser fix is a rebuild, not a data migration ([`docs/normalization.md`](docs/normalization.md)).
- **A pure domain core.** Parsing, normalization, the same-job rule and subscription matching are pure functions in `domain/`, type-checked strictly and covered by tests, with a CI gate at 90%.
- **Deduplication prefers a missed duplicate to a wrong merge,** because a wrong merge hides a job from subscribers. Checked against 433 hand-labelled pairs from Djinni and DOU: it joins 327 of 370 duplicate pairs and merges no different jobs ([`docs/deduplication.md`](docs/deduplication.md)).
- **The database enforces idempotency.** Unique keys with `ON CONFLICT` make every collection and every task safe to run twice, and each match reaches Telegram at most once ([`docs/telegram.md`](docs/telegram.md#delivery)).
- **Collection follows each source's terms.** RSS rather than scraping; DOU is fetched at most every 30 minutes and never retried, as its consent requires ([`docs/sources.md`](docs/sources.md)).
- **An external cron instead of GitHub's `schedule`,** which started this workflow 2 times in about 14 hours on its first night, while DOU's feed moved past unseen jobs ([`docs/collection.md`](docs/collection.md#triggers)).
- **Full-text search in Postgres** with the `simple` configuration and `unaccent`, because the texts mix Ukrainian and English and Postgres has no Ukrainian configuration ([`docs/search.md`](docs/search.md)).
- **Analytics show their sample size.** Every number carries its `n`, a salary median of fewer than 10 jobs is hidden, and periods still being collected are marked ([`docs/analytics.md`](docs/analytics.md#showing-them-honestly)).
- **Small dependencies.** Telegram is plain httpx against the Bot API, without a bot framework; the pages are server-rendered Jinja2 with HTMX, without a JavaScript build.

## Quickstart

Requires Docker with Compose v2.

```sh
docker compose up --build
```

This starts the API, a Celery worker, Celery Beat, the Telegram bot, PostgreSQL 16 and Redis. The bot needs a token from @BotFather in `.env` and exits without one (see [`docs/telegram.md`](docs/telegram.md)). Then check:

```sh
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}
```

The site is at http://localhost:8000, where anyone can search vacancies at `/vacancies` ([`docs/search.md`](docs/search.md)), see market analytics at `/analytics` ([`docs/analytics.md`](docs/analytics.md)), and you can sign up and sign in. Before the app services start, a one-shot `migrate` service applies the Alembic migrations, so the same command works on an empty database and after pulling new migrations. New files in `migrations/` are picked up without a rebuild. How accounts, cookies and CSRF tokens work is described in [`docs/auth.md`](docs/auth.md), subscriptions in [`docs/subscriptions.md`](docs/subscriptions.md), and linking Telegram in [`docs/telegram.md`](docs/telegram.md).

To override settings, copy `.env.example` to `.env` and edit it.

The database starts empty. The worker fetches Djinni within 5 minutes, and a vacancy shows in search once it is normalized (every 10 minutes) and given a cluster (every 5); the analytics are refreshed every 30. From a fresh clone, the first 100 Djinni jobs were searchable after about 15 minutes. DOU is collected only into the hosted database (see [Background collection](#background-collection-celery)).

### Real data for a demo

A backup of the hosted database ([Scheduled collection](#scheduled-collection)) fills a separate local database with every vacancy collected so far. Only the raw items are restored: the schema comes from this checkout's migrations, and everything else is rebuilt from the raw items. With the Compose Postgres running and a backup in `<backup>.dump`:

```sh
docker compose exec postgres createdb -U vacancy_radar vacancy_radar_demo
export DATABASE_URL=postgresql+psycopg://vacancy_radar:vacancy_radar@localhost:5432/vacancy_radar_demo
uv run alembic upgrade head
for table in source raw_vacancy; do
  docker compose exec -T postgres pg_restore --data-only --table "$table" \
    -U vacancy_radar -d vacancy_radar_demo < <backup>.dump
done
uv run python -m vacancy_radar.normalize    # raw items → vacancies, with NBU exchange rates
uv run python -m vacancy_radar.clusters     # one cluster per job, sends nothing
uv run python -m vacancy_radar.analytics    # fill the analytics views
uv run uvicorn vacancy_radar.api.app:app --port 8001
```

The site on http://localhost:8001 then shows the real data. No worker runs against this database, so nothing is collected into it and nothing is sent to Telegram. The `SECRET_KEY` comes from `.env` (copied from `.env.example`). On the backup of 2026-11-15 (4439 raw items) the three commands took about 80 seconds. To start over, drop the database with `docker compose exec postgres dropdb -U vacancy_radar vacancy_radar_demo`.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```sh
uv sync                          # create .venv with all dependencies
uv run pre-commit install        # run linters on every commit
cp .env.example .env             # settings for running on the host, SECRET_KEY included
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
| Normalize new and changed items into `vacancy` | `uv run python -m vacancy_radar.normalize` |
| Rebuild every vacancy after a parser change | `uv run python -m vacancy_radar.normalize --rebuild` |
| Give new vacancies a cluster (deduplication), without sending | `uv run python -m vacancy_radar.clusters` |
| Refresh the analytics views ([`docs/analytics.md`](docs/analytics.md)) | `uv run python -m vacancy_radar.analytics` |
| New migration after a model change | `uv run alembic revision --autogenerate -m "<what changed>"` |

Tests run against a real PostgreSQL database, because the project relies on FTS, GIN indexes, `pg_trgm` and `ON CONFLICT`. By default they use `vacancy_radar_test` on the Compose Postgres, create it on first run and apply the Alembic migrations to it. Each test runs inside a transaction that is rolled back afterwards. Set `TEST_DATABASE_URL` to use another server. The suite refuses to run against a database whose name does not end in `_test`. Tests that need Redis use database 15 on the Compose Redis and empty it around each test. Set `TEST_REDIS_URL` to use another one, but never the broker database of a running worker.

## Collecting vacancies

`python -m vacancy_radar.collect` fetches each source once and stores every item unchanged in `raw_vacancy`. Running it again is safe: new items are inserted, items whose content changed are updated, and identical items are left alone. Each run logs one `collect.finished` line per source:

```json
{"source": "djinni", "fetched": 100, "skipped": 0, "inserted": 3, "updated": 0, "unchanged": 97, "event": "collect.finished", "level": "info", "timestamp": "..."}
```

`unchanged + updated` is the overlap with earlier runs. If it drops to 0, the feed moved on between runs and some vacancies were missed, so poll more often. Logs are JSON when stderr is not a terminal (CI, containers) and readable console output otherwise. The exit code is 1 if any source failed. See [`docs/sources.md`](docs/sources.md) for what each source allows.

### Scheduled collection

While the production deploy is deferred, the [`Collect`](.github/workflows/collect.yml) GitHub Actions workflow runs every 15 minutes, started by an external cron (see [Trigger](#trigger)). It applies pending migrations and then runs the collector against a free [Neon](https://neon.com) Postgres 16 database. The connection URL is the `DATABASE_URL` repository secret, in the form `postgresql+psycopg://<role>:<password>@<host>/<database>?sslmode=require&channel_binding=require`. This is the direct (not `-pooler`) connection string from the Neon console, with the scheme changed to `postgresql+psycopg://`. The role and database names are the ones the console shows. For running commands against Neon locally, keep the same line in a gitignored `.env.neon` and pass `uv run --env-file .env.neon …`. The job needs no Redis. A failing source turns the run red.

Each source sets a `min_interval`: Djinni 25 minutes, DOU 30 minutes, the limit in DOU's consent. A source is skipped until that much time has passed since its last request, as recorded in `collect_run`. So cron delays never make a request too early. A source that becomes due within 2 minutes is waited for rather than skipped, so a job that starts a few seconds early still fetches DOU 30 minutes after its last request. To stop a source without a code change, set the `DISABLED_SOURCES` repository variable, e.g. to `dou` (Settings → Secrets and variables → Actions → Variables).

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

A backup also fills a local database for a demo ([Real data for a demo](#real-data-for-a-demo)). If the deploy happens, the data moves to the VPS (stage 11 in [`PLAN.md`](PLAN.md)).

### Background collection (Celery)

In production (stage 11, deferred), Celery would replace the GitHub Actions job. Locally, `docker compose up` already runs it:

- **Beat** sends one `collect.fetch_source` task per source every 5 minutes. A message the worker hasn't picked up within 4 minutes is dropped, so tasks don't pile up while the worker is down. Every 10 minutes it also sends `normalize.pending`, which turns new and changed raw items into `vacancy` rows ([`docs/normalization.md`](docs/normalization.md)).
- **The worker** runs the same `collect_if_due` as the CLI. It skips a disabled source, a source another collector is starting a run for right now (a Postgres advisory lock), and a source whose `min_interval` hasn't passed. The run is recorded in `collect_run` before the request is sent, so every request counts against `min_interval`. Djinni is fetched every 25–30 minutes and DOU every 30–35.
- **Rate limit:** every request first takes a token from a Redis token bucket for its domain, at most 1 request per second across all worker processes. If no token comes within 10 seconds, no request is sent, but the run is still recorded as `failed` (`RateLimitError`): Djinni retries it like a timeout, and DOU waits for its next due run.
- **Retries:** timeouts, network errors and 5xx are retried with exponential backoff (from 30 s, capped at 10 min) and full jitter, honouring a numeric `Retry-After`. Each source sets its own `max_retries`: Djinni 3, DOU 0, because DOU's consent counts retries as requests. A retry skips `min_interval` only while the failed run it repeats is still the source's latest, so a retry delivered twice sends no request. 403 and 429 are never retried and are logged as `collect.blocked`.

Compose sets `DISABLED_SOURCES=dou` for the worker. DOU is collected into Neon by GitHub Actions, and a second local collector would break DOU's limit of one request per 30 minutes. The worker therefore collects only Djinni, into the local database.

| Task | Command |
|---|---|
| Worker logs | `docker compose logs -f worker` |
| Run a collection now | `docker compose exec worker celery -A vacancy_radar.worker call collect.fetch_source --args='["djinni"]'` |
| Registered tasks | `docker compose exec worker celery -A vacancy_radar.worker inspect registered` |

## Deployment (deferred)

A production deploy to a small VPS (stage 11 in [`PLAN.md`](PLAN.md)) is deferred: there is no budget for hosting yet. Collection into Neon keeps running, so the raw data a deploy would start from keeps accumulating.

Already prepared:

- The image carries the migrations, and a one-shot `migrate` service applies them before the app starts.
- In production the web app, worker and bot refuse to start with a development `SECRET_KEY` or without an https site URL, and cookies are `Secure`.
- The API logs JSON, hides `/docs` in production, and uses no inline styles or scripts, so a Content Security Policy can forbid them.
- CI builds the image and checks `/health` in it.
- Celery collection, with rate limiting and retries, is ready to replace the GitHub Actions job.

Still to do: a production Compose file with Caddy for HTTPS, the server setup, nightly backups with a tested restore, a deploy workflow, moving the data from Neon, rate limits on sign-in, sign-up and search, and CSP and HSTS headers.

## Project layout

```text
src/vacancy_radar/
├── analytics/  # market analytics: materialized views of jobs by week and month, and reading them
├── api/        # FastAPI app, routes and Jinja2 templates
├── auth/       # password hashing, session tokens, CSRF tokens, registration and sign-in
├── clusters/   # deduplication: each new vacancy joins the cluster of the same job, or opens one
├── collect/    # due checks, idempotent storage, the collector CLI and the Celery task
├── db/         # SQLAlchemy models and sync/async engines
├── domain/     # pure functions: parsing, normalization, matching, deduplication (no I/O)
├── exchange/   # NBU exchange rates, cached in exchange_rate
├── normalize/  # raw_vacancy → vacancy: the reprocessing CLI and the Celery task
├── notify/     # matched vacancies sent to Telegram: matching, messages and delivery
├── search/     # vacancy search for the site: full-text search and filters, one result per job
├── sources/    # one adapter per source (fetch + parse), behind SourceAdapter
├── subscriptions/  # storing each account's subscriptions, scoped to the owner
├── telegram/   # Bot API client, one-time link tokens, and the long-polling bot
├── config.py   # settings from environment / .env
├── log.py      # structlog setup
├── ratelimit.py  # Redis token bucket per domain, shared by source adapters and the Telegram client
└── worker.py   # Celery app for the worker and Beat
migrations/     # Alembic migrations (the only way the schema changes)
scripts/        # developer tools run by hand, e.g. measuring deduplication on a backup
tests/          # pytest; fixtures/ holds saved source responses
```

## License

[MIT](LICENSE). The license covers the code. Vacancy data belongs to the sites it comes from and is used as each one allows ([`docs/sources.md`](docs/sources.md)).
