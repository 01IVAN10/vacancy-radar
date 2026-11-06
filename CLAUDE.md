# Vacancy Radar

IT job aggregator with market analytics. Collects vacancies from RSS feeds and public sources (DOU, Djinni, Work.ua), deduplicates and normalizes them (stack, salary, seniority, work format), delivers subscription matches to Telegram, and shows market analytics.

It is a portfolio project for Python Developer / Data Analyst roles, and recruiters and engineers will read the code. Optimize for readable, idiomatic, well-tested code over cleverness. The author also uses the service to look for a job.

Roadmap and progress: `PLAN.md`. Before starting work, find the current stage there. Tick its checkboxes as part of the same PR.

## MVP definition of done
- Collection from ≥1 source runs on schedule in production.
- Duplicates of one vacancy across sources are grouped into one cluster.
- A user can register, create a subscription, link Telegram, and receive a match exactly once.
- `/analytics` shows: top skills per month, salary ranges per week, new vacancies per week.
- CI is green; coverage of `domain/` ≥ 90%.
- README gets a new developer to a running stack with one command.

## Stack
Python 3.12, uv, FastAPI, Jinja2 + HTMX + Chart.js (CDN), PostgreSQL 16 (FTS, GIN, pg_trgm, unaccent), SQLAlchemy 2.0 + Alembic, psycopg 3, Celery + Celery Beat, Redis, httpx, feedparser, pydantic-settings, structlog, pytest + respx, ruff, mypy, pre-commit, Docker Compose, Caddy, GitHub Actions.

Hosting: a ~€4/month VPS. Until it exists, a GitHub Actions cron workflow runs the collector CLI against a free hosted Postgres (Neon/Supabase), because RSS keeps no history. That data moves to the VPS at deploy time.

## Architecture decisions
- **Raw first.** Every fetched item is stored unchanged in `raw_vacancy` (JSONB). Normalized tables are derived from it and can be rebuilt at any time when parsers improve.
- **Collection is independent of Celery.** Adapters and the upsert logic are plain functions behind `SourceAdapter`. The CLI (`python -m vacancy_radar.collect`) and the Celery task are thin wrappers around them.
- **Async API, sync workers.** FastAPI uses the async engine and Celery tasks use the sync engine. Both run on psycopg 3 and share the same models.
- **Pure domain core.** Normalization, salary parsing, skill extraction, dedup scoring, and subscription matching live in `src/vacancy_radar/domain/` as pure functions with no I/O.
- **Idempotency is enforced by the database**: unique `(source_id, external_id)` with `ON CONFLICT` upserts; unique `(subscription_id, cluster_id)` on notifications. Every task is safe to run twice.
- **Retries:** Celery `self.retry` with exponential backoff and full jitter, only for transient failures (timeouts, network errors, 5xx). Each source sets its own `max_retries`, and DOU's is 0 because its consent counts retries as requests. **Rate limiting:** a Redis token bucket per source domain.
- **Dedup is conservative**: a missed duplicate is better than merging two different vacancies, because a wrong merge hides a vacancy from subscribers.
- **FTS** uses the `simple` config + `unaccent`, because the texts mix Ukrainian and English and Postgres ships no Ukrainian config. Skills are stored as `text[]` with a GIN index.
- **Analytics shows sample size**: every aggregate carries its `n`, and medians below the minimum sample are hidden.
- **Telegram** is called through plain httpx against the Bot API, without a bot framework.
- **Auth:** email + password (argon2 via pwdlib), JWT in an httpOnly cookie, CSRF tokens on forms. Telegram is linked with a one-time deep-link token.
- Add a dependency only with a one-line justification in the PR description.

## Legal data collection
Source order of preference: official API → RSS → HTML scraping. Scrape HTML only where both the site's ToS and robots.txt allow it. Record each source's status in `docs/sources.md` before writing its adapter. Identify with a generic project User-Agent, respect the rate limiter, and keep request volume minimal. Public pages show title, normalized fields, a short excerpt, and a link to the original rather than republishing full descriptions.

## Code rules
- ruff (lint + format) and mypy must pass. `domain/` is mypy-strict.
- Every behaviour change comes with tests. Tests run against real Postgres, not SQLite, because FTS, GIN, `ON CONFLICT`, and pg_trgm need it. Mock HTTP with respx using saved fixtures in `tests/fixtures/`.
- Code, comments, commits, and docs are in English. Discussion with the author is in Ukrainian.
- Schema changes go through Alembic migrations only.
- Secrets live in `.env` locally and in GitHub Secrets in CI; only `.env.example` is committed.

## Git flow
- `main` is always deployable. Commit to it directly only for the initial bootstrap commit.
- Each feature or fix gets its own branch `feat/<name>`, `fix/<name>`, or `chore/<name>`, then a PR into `main`. Merge only when CI is green.
- Use Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).
- **Recommend, then wait for confirmation.** When a logical unit of work is finished and tests and linters pass, suggest a commit (proposed message + files), a push, or opening a PR. Run git commit, push, PR creation, and merge only after the author confirms that specific action.
- Before saying "done", run CI's checks locally (`uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`) and report the results. Plain `uv run mypy` type-checks `tests/` too, as CI does.
