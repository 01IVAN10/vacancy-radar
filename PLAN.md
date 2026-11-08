# Vacancy Radar: roadmap

Each stage is one or more PRs (`feat/<name>` → `main`). Tick boxes in the PR that completes them.

Order follows a vertical slice: start accumulating raw data as early as possible (RSS keeps no history), then build a working version for personal use, then the showcase features and production deploy.

## Current state

Updated 2026-11-08.

- **Stage 3** is done except for verification. Djinni and DOU are collected into Neon, started every 15 minutes by cron-job.org because GitHub drops scheduled runs (see `docs/collection.md`).
- **Stage 4** is done: Celery collection with Beat, retries, and a Redis rate limiter. It runs locally in Compose for Djinni only, and in production from stage 11, when it replaces the GitHub Actions job.
- **Stage 5** is done: `raw_vacancy` is normalized into `vacancy` (salary in its currency and in monthly USD, seniority, work format, skills, DOU company and places) by `python -m vacancy_radar.normalize` and the Celery task `normalize.pending`. See `docs/normalization.md`. It runs locally only. Neon keeps accumulating raw data, and vacancies are rebuilt from it on the VPS (stage 11).
- **Stage 6** is done: accounts with sign-in and CSRF protection (`docs/auth.md`), and subscription pages at `/subscriptions` (`docs/subscriptions.md`).
- **Stage 7** is done: subscription matching (#24), Telegram linking (#25) and notifications. `notify.match` gives each vacancy a cluster of its own and records the matches in `notification`, unique on `(subscription_id, cluster_id)`, with one `delivery` per account and cluster; `notify.send` sends each delivery at most once, with 429 handling (`docs/telegram.md`, Delivery). The bot's restart behaviour and delivery end to end were checked by hand on 2026-11-08: a Djinni run brought 46 new vacancies, 14 matched, and each arrived in Telegram once.
- **Problems on the way:** the review found that unknown seniority, format and salary let a one-criterion subscription match almost every vacancy, and that the bot re-answered its last batch after every restart. Both are fixed. A salary not yet converted from UAH would have read as no salary, so matching waits up to an hour for conversion. A request that timed out after it was sent may have been delivered, so it is not retried.
- **Next:** stage 8 (deduplication). In parallel, the findings PR (`chore/collection-findings`), not before ~2026-11-11, after at least 5 days that include a weekend. It runs the queries in `docs/collection.md`, writes the measured intervals into `docs/sources.md`, closes open question 3, and ticks "Verify data arrives over several days".

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

Decisions (2026-11-06): `collect_if_due` is shared by the CLI and the task. It applies the kill switch, a Postgres advisory lock per source, and `min_interval`. Retries are per source (`max_retries`: Djinni 3, DOU 0, because DOU's consent counts retries as requests) and skip only the `min_interval` check. Fix after review (2026-11-07): a run is committed as `running` before its request, so any error after the request still counts against `min_interval`. A retry carries the id of the failed run it repeats (`retry_of`) and runs only while that run is the source's latest. Beat offers each source every 5 minutes. Locally, Compose disables DOU, which GitHub Actions collects into Neon until stage 11.

### Stage 5: Normalization
- [x] Models `vacancy`, `skill`
- [x] Salary parser: ranges, "від/до", UAH/USD/EUR
- [x] NBU exchange rates client, cached daily
- [x] Seniority and work-format detection
- [x] Skill dictionary with synonyms + extraction
- [x] Reprocessing task `raw_vacancy` → `vacancy`
- [x] Parametrized unit tests built from real collected examples

Fix after review (2026-11-06): benefits with an amount and a period ("insurance compensation up to $150 per month") are no longer read as a salary, `з/п` is a pay word, and "Go/No-Go", "RAG status" and "airflow sensors" are not skills. `NORMALIZER_VERSION` is 2, and a test pins it to a hash of the parser modules so a parser change cannot ship without a decision about the version. Salaries are converted to dollars in a separate step after normalization (`convert_salaries`). Normalization no longer calls NBU, so an NBU outage leaves only `salary_usd_*` NULL until the next run instead of stopping every batch.

### Stage 6: Auth and subscriptions
- [x] Models `user`, `subscription`
- [x] Registration and login (argon2, JWT in httpOnly cookie)
- [x] CSRF protection for forms
- [x] Base Jinja2 layout + HTMX
- [x] Subscription CRUD (skills, seniority, format, min salary USD)
- [x] Tests: auth flow, permissions, filter validation

Decisions (2026-11-07): the account table is `app_user`, because `user` is reserved in Postgres. Emails are stored lower-cased, and a CHECK enforces it. Sessions are a 7-day HS256 JWT with no server-side revocation, and CSRF uses double-submit cookies. Details are in `docs/auth.md`. Subscriptions carry `skills_match` (`any` | `all`) and `include_unsalaried` (default true), because only about 5% of vacancies state a salary. At least one criterion is required, an account has at most 20 subscriptions, and another account's id gives 404. Details are in `docs/subscriptions.md`. Pages use Pico.css and HTMX from a CDN, pinned with SRI.

### Stage 7: Telegram
- [x] Bot API client on httpx
- [x] Deep-link account linking with a one-time token
- [x] Subscription matching in `domain/`
- [x] Model `notification` with unique `(subscription_id, cluster_id)`
- [x] Send task with Telegram 429 handling
- [x] Tests: matching, no duplicate sends on rerun

Decisions (2026-11-07):
- **Matching.** Each criterion is met, failed or unknown. A vacancy matches when none failed and at least one was met: an unknown level, format or missing salary never rules a vacancy out, but cannot be the only reason to send it. A vacancy without skills fails a skills filter. A salary matches when the top of its monthly USD range reaches the minimum.
- **Telegram linking.** The bot uses long polling, not a webhook. Link tokens are stored as SHA-256, live 15 minutes and are used once (`DELETE … RETURNING`). One chat belongs to one account. The bot confirms its last batch when it stops, and a rejected token stops it for good (exit 0).
- **Delivery (part 3).** Only clusters first seen after both the subscription became active and the chat was linked are sent.

Decisions (2026-11-08):
- **Clusters now.** `vacancy_cluster` exists already, with one vacancy per cluster, so notifications are keyed by cluster from the start. A cluster's `first_seen_at` comes from its raw item. `subscription.active_since` moves on create and resume, not on edit.
- **What is matched.** Only clusters first seen within the last 2 days. A UAH or EUR salary waits up to an hour for NBU conversion before it counts as unknown.
- **Matches and deliveries.** `notification` is a match (unique subscription × cluster); `delivery` is the message (unique account × cluster). A subscription that matches after the message went out joins that delivery, so the database keeps a vacancy from reaching a chat twice (found in review). A delivery has no `vacancy_id`: the vacancy shown is picked from the cluster at send time, so rebuilding vacancies cannot erase what was sent.
- **At most once.** A delivery is committed as `sending` before the request. A request that may have reached Telegram (read timeout, dropped connection, any 5xx) is marked `failed` with "outcome unknown" instead of being retried; during a Telegram outage that costs one delivery per run. 403 and 400 "chat not found" unlink the chat. The failure policy is one pure function, `notify/outcome.py`.
- **No Celery retries for sending.** `delivery.next_attempt_at` holds the backoff, Beat sends `notify.send` every minute, and an advisory lock keeps it to one sender. The rate limiter moved to `ratelimit.py`, shared by sources and Telegram; a Redis failure counts as "no token", so the request is known not to have left.

## Phase C: Showcase features and production

### Stage 8: Deduplication
- [x] Model `vacancy_cluster` (added in stage 7: one vacancy per cluster, set by `clusters/service.py`; this stage replaces that rule. `notification.cluster_id` is RESTRICT, so merging clusters must decide what happens to their notifications)
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
- [ ] Rate-limit sign-in and sign-up (Redis token bucket), CSP and HSTS in Caddy
- [ ] Smoke check after deploy

### Stage 12: Polish
- [ ] README: architecture diagram, screenshots, quickstart, decisions and trade-offs
- [ ] CI and coverage badges
- [ ] Verify the MVP definition of done in `CLAUDE.md`

## Deferred (post-MVP)
Email notifications, Work.ua and other sources, Jupyter notebook with market research, admin panel, ML seniority classification, scheduled digests, Sentry/Prometheus, UI i18n.
