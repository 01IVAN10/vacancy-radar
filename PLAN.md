# Vacancy Radar: roadmap

Each stage is one or more PRs (`feat/<name>` → `main`). Tick boxes in the PR that completes them.

Order follows a vertical slice: start accumulating raw data as early as possible (RSS keeps no history), then build a working version for personal use, then the showcase features and production deploy.

## Current state

Updated 2026-11-06.

- **Stage 3** is done except for verification. Djinni and DOU are collected into Neon, started every 15 minutes by cron-job.org because GitHub drops scheduled runs (see `docs/collection.md`).
- **Next:** the findings PR (`chore/collection-findings`), not before ~2026-11-11, after at least 5 days that include a weekend. It runs the queries in `docs/collection.md`, writes the measured intervals into `docs/sources.md`, closes open question 3, and ticks "Verify data arrives over several days".
- **Stage 4** is done: Celery collection with Beat, retries, and a Redis rate limiter. It runs locally in Compose for Djinni only, and in production from stage 11, when it replaces the GitHub Actions job.
- **After the findings PR:** stage 5 (normalization).

## Phase A: Start accumulating data

### Stage 0: Source research
- [x] DOU: RSS feeds, ToS, robots.txt
- [x] Djinni: RSS/API, ToS, robots.txt
- [x] Work.ua: API, ToS, robots.txt
- [x] Findings in `docs/sources.md`: allowed method, URLs, rate limits, available fields
- [x] Sample feeds saved as test fixtures (Djinni; DOU fixtures came with its adapter in stage 3)

Outcome: Djinni is the first source. DOU's terms require consent for automated collection. Consent was granted in writing on 2026-11-05 on the conditions in `docs/sources.md`, and DOU stays the second source (stage 10). Work.ua is not allowed.

### Stage 1: Skeleton
- [x] uv project, `src/vacancy_radar/` layout, `pyproject.toml`
- [x] ruff, mypy, pre-commit config
- [x] pydantic-settings config + `.env.example`
- [x] FastAPI app with `/health`
- [x] Docker Compose: api, worker, beat, postgres, redis
- [x] pytest with a Postgres test database
- [x] GitHub Actions CI: ruff, mypy, pytest (Postgres + Redis services)

### Stage 2: Raw data + Djinni adapter
- [x] Models `source`, `raw_vacancy` (JSONB payload, unique `(source_id, external_id)`)
- [x] Sync and async engines on psycopg 3
- [x] Initial Alembic migration
- [x] `SourceAdapter` interface
- [x] Djinni RSS adapter (httpx + feedparser)
- [x] Idempotent upsert
- [x] CLI `python -m vacancy_radar.collect`
- [x] Tests: adapter on fixtures (respx), double-run idempotency

### Stage 3: Early collection (free)
- [x] Choose free hosted Postgres (Neon or Supabase) after checking current limits
- [x] Run migrations against it
- [x] GitHub Actions cron workflow running the CLI every 30 min, `DATABASE_URL` in GH Secrets
- [x] DOU adapter (all-jobs feed, consent limits: `min_interval` 30 min, `DISABLED_SOURCES` kill switch), started early because RSS keeps no history
- [x] Schedule every 15 min; each source is fetched only when its `min_interval` has passed
- [x] Run log `collect_run` (status, counts, error per attempt) + checking queries in `docs/collection.md`
- [x] External trigger (cron-job.org → `workflow_dispatch`), because GitHub drops scheduled runs
- [ ] Verify data arrives over several days

Decision (2026-11-05): Neon Free, Postgres 16, region `eu-central-1`. It has 1 GB of storage per project and 100 CU-hours a month, which is enough for about 30 CU-hours a month of collection. It has no inactivity pause and accepts IPv4 connections from GitHub runners. Supabase Free has 500 MB, pauses after 7 days of inactivity, and accepts direct connections only over IPv6. Postgres 16 matches the VPS, so `pg_dump` → `pg_restore` works in stage 11. Migrations run as the first step of every scheduled job.

## Phase B: Working version for personal use

### Stage 4: Celery collection
- [x] Celery app + `fetch_source` task wrapping the same collection code
- [x] Celery Beat schedule
- [x] Retry with exponential backoff and jitter
- [x] Redis token-bucket rate limiter per domain
- [x] Tests: retry on 5xx/timeout, rate limiter, task idempotency

Decisions (2026-11-06): `collect_if_due` is shared by the CLI and the task. It applies the kill switch, a Postgres advisory lock per source, and `min_interval`. Retries are per source (`max_retries`: Djinni 3, DOU 0, because DOU's consent counts retries as requests) and skip only the `min_interval` check. Beat offers each source every 5 minutes. Locally, Compose disables DOU, which GitHub Actions collects into Neon until stage 11.

### Stage 5: Normalization
- [ ] Models `vacancy`, `skill`
- [ ] Salary parser: ranges, "від/до", UAH/USD/EUR
- [ ] NBU exchange rates client, cached daily
- [ ] Seniority and work-format detection
- [ ] Skill dictionary with synonyms + extraction
- [ ] Reprocessing task `raw_vacancy` → `vacancy`
- [ ] Parametrized unit tests built from real collected examples

### Stage 6: Auth and subscriptions
- [ ] Models `user`, `subscription`
- [ ] Registration and login (argon2, JWT in httpOnly cookie)
- [ ] CSRF protection for forms
- [ ] Base Jinja2 layout + HTMX
- [ ] Subscription CRUD (skills, seniority, format, min salary USD)
- [ ] Tests: auth flow, permissions, filter validation

### Stage 7: Telegram
- [ ] Bot API client on httpx
- [ ] Deep-link account linking with a one-time token
- [ ] Subscription matching in `domain/`
- [ ] Model `notification` with unique `(subscription_id, cluster_id)`
- [ ] Send task with Telegram 429 handling
- [ ] Tests: matching, no duplicate sends on rerun

## Phase C: Showcase features and production

### Stage 8: Deduplication
- [ ] Model `vacancy_cluster`
- [ ] Exact fingerprint (normalized company + title + city)
- [ ] Fuzzy match: pg_trgm candidates → rapidfuzz scoring, conservative threshold
- [ ] Cluster assignment task
- [ ] Tests on real duplicate / non-duplicate pairs

### Stage 9: Search and analytics
- [ ] FTS `search_vector` (`simple` + `unaccent`), GIN on it and on `skills`, trigram on title
- [ ] Vacancy search page: FTS + filters + pagination
- [ ] Materialized views: top skills per month, salary ranges per week, new vacancies per week (each with `n`)
- [ ] Periodic refresh task
- [ ] `/analytics` page with Chart.js, medians hidden below the minimum sample
- [ ] `docs/analytics.md`: queries, EXPLAIN ANALYZE before/after indexes, findings

### Stage 10: Second source
- [x] Written consent from DOU recorded in `docs/sources.md` (request to `legal@dou.ua`)
- [x] Adapter for the second allowed source (DOU once consented, per `docs/sources.md`) + its fixtures (done early, in stage 3)
- [ ] Cross-source dedup verified on real data

### Stage 11: Deploy to VPS
- [ ] Production Compose file + Caddy (HTTPS)
- [ ] VPS setup: Docker, firewall, non-root user; Postgres and Redis not exposed, Redis password
- [ ] Nightly `pg_dump` backup + tested restore
- [ ] GitHub Actions deploy over SSH, secrets in GH Secrets
- [ ] Migrate data from the free Postgres (`pg_dump` → `pg_restore`), disable the cron workflow
- [ ] Delete the cron-job.org job and revoke its token
- [ ] Smoke check after deploy

### Stage 12: Polish
- [ ] README: architecture diagram, screenshots, quickstart, decisions and trade-offs
- [ ] CI and coverage badges
- [ ] Verify the MVP definition of done in `CLAUDE.md`

## Deferred (post-MVP)
Email notifications, Work.ua and other sources, Jupyter notebook with market research, admin panel, ML seniority classification, scheduled digests, Sentry/Prometheus, UI i18n.
