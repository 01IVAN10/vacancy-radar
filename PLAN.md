# Vacancy Radar: roadmap

Each stage is one or more PRs (`feat/<name>` → `main`). Tick boxes in the PR that completes them.

Order follows a vertical slice: start accumulating raw data as early as possible (RSS keeps no history), then build a working version for personal use, then the showcase features and production deploy.

## Current state

Updated 2026-11-09.

- **Stage 3** is done except for verification. Djinni and DOU are collected into Neon, started every 15 minutes by cron-job.org because GitHub drops scheduled runs (see `docs/collection.md`).
- **Stage 4** is done: Celery collection with Beat, retries, and a Redis rate limiter. It runs locally in Compose for Djinni only, and in production from stage 11, when it replaces the GitHub Actions job.
- **Stage 5** is done: `raw_vacancy` is normalized into `vacancy` (salary in its currency and in monthly USD, seniority, work format, skills, DOU company and places) by `python -m vacancy_radar.normalize` and the Celery task `normalize.pending`. See `docs/normalization.md`. It runs locally only. Neon keeps accumulating raw data, and vacancies are rebuilt from it on the VPS (stage 11).
- **Stage 6** is done: accounts with sign-in and CSRF protection (`docs/auth.md`), and subscription pages at `/subscriptions` (`docs/subscriptions.md`).
- **Stage 7** is done: subscription matching (#24), Telegram linking (#25) and notifications. `notify.match` gives each vacancy a cluster of its own and records the matches in `notification`, unique on `(subscription_id, cluster_id)`, with one `delivery` per account and cluster; `notify.send` sends each delivery at most once, with 429 handling (`docs/telegram.md`, Delivery). The bot's restart behaviour and delivery end to end were checked by hand on 2026-11-08: a Djinni run brought 46 new vacancies, 14 matched, and each arrived in Telegram once.
- **Problems on the way:** the review found that unknown seniority, format and salary let a one-criterion subscription match almost every vacancy, and that the bot re-answered its last batch after every restart. Both are fixed. A salary not yet converted from UAH would have read as no salary, so matching waits up to an hour for conversion. A request that timed out after it was sent may have been delivered, so it is not retried.
- **Before stage 8:** the architecture review of `main` (2026-11-08) is done. Its prep PRs are merged: a CI gate for `domain/` coverage ≥ 90% (#28), clustering in its own package `clusters/` with notify tests independent of it (#29), and the stage 8 rules below.
- **Stage 8** is done: vacancies keep their plain description (#33), the same-job rule is measured on real data (#34, `domain/dedup.py`), and `notify.match` puts each new vacancy into the cluster of the same job (`clusters/service.py`, `docs/deduplication.md`). On the 2026-11-09 sample, 77 jobs were posted twice or three times, 67 of them on both Djinni and DOU. It runs locally only; Neon keeps collecting raw data, and the VPS clusters it in stage 11.
- **Next:** stage 9 (search and analytics). In parallel, the findings PR (`chore/collection-findings`), not before ~2026-11-11, after at least 5 days that include a weekend. It runs the queries in `docs/collection.md`, writes the measured intervals into `docs/sources.md`, closes open question 3, and ticks "Verify data arrives over several days". It also measures how far apart reposts of one job are, to check the 30-day candidate window of deduplication (`scripts/dedup_sample.py measure` on a new backup).

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
- [x] Model `vacancy_cluster` (added in stage 7 with one vacancy per cluster; clusters are never merged, see Decisions)
- [x] Same-job rule: title words + description shingles, with vetoes (`domain/dedup.py`). It replaces the exact company + title + city fingerprint, which found nothing in real data (see Decisions 2026-11-09)
- [x] Candidates from a pg_trgm index on titles, judged by the rule (no rapidfuzz)
- [x] Cluster assignment: `assign_clusters` in `notify.match` and `python -m vacancy_radar.clusters`, one run at a time
- [x] Tests on real duplicate / non-duplicate pairs

Decisions (2026-11-08, before implementation):
- **Attach-only.** A new vacancy joins an existing cluster or opens a new one; two existing clusters are never merged. So `notification.cluster_id` and `delivery.cluster_id` (both RESTRICT) and the unique `delivery (user_id, cluster_id)` need no changes. If merging is ever needed, it gets a "merged into" pointer, not repointed foreign keys. This is the conservative side: a missed duplicate is better than a wrong merge.
- **One clustering run at a time.** An advisory lock is held for the whole run. Per-row SKIP LOCKED is not enough: two runs could open two clusters for the two halves of one duplicate pair.
- **`cluster_id` never changes once assigned,** re-normalization included. Re-clustering an old vacancy would give it a new cluster and so a new delivery (account × cluster), which is a repeat send.
- **A cluster matches when any of its vacancies matches** the subscription (today's behaviour, now explicit). **The message shows the most complete vacancy:** one with a salary first, then one with a company, then the lowest id. It is picked when the delivery is claimed, as now (`notify/sending.py:_card`, which today takes the lowest id; `docs/telegram.md` changes with it).
- **A new member sends nothing.** A cluster's `first_seen_at` is the minimum over its vacancies, so a vacancy that joins an old cluster stays behind the matching watermark. This is intended: it is the same job. (Refined on 2026-11-09, see below: a new member can match a subscription the earlier ones did not, but never sends twice.)
- **Facts for the implementation:**
  - Djinni vacancies have no company (0/348), no cities (0/348) and rarely a salary (21/348) in the local DB on 2026-11-08, so an exact company + title + city fingerprint cannot work for Djinni. DOU takes company and cities from its title.
  - Real DOU data is only in Neon. Duplicate pairs for tests come from a copy of it (README, backing up the hosted database).
  - No migration creates `pg_trgm` yet; `tests/db/test_database.py` only checks that it is available. (Migration 0014 creates it.)
  - `rapidfuzz` is a new dependency and needs a one-line justification in its PR. (Not needed after all, see 2026-11-09.)

Decisions (2026-11-09, measured on the Neon backup of that day, details in `docs/deduplication.md`):
- **The description decides, the title and vetoes guard it.** Two vacancies are the same job when 85% of their 4-word description shingles are shared, their titles agree (same words within one source, one a subset of the other across sources), and no veto applies (different levels, two different known companies, no shared place, salaries in one currency that cannot overlap). On 1644 items this gives 77 clusters with several vacancies (67 Djinni + DOU) and no wrong merge found. Text alone would have merged 87 more pairs, mostly different roles of one company.
- **Complete linkage.** A vacancy joins a cluster only if it is the same job as every member.
- **Candidates by title trigrams.** A new vacancy is compared only with vacancies whose titles match it under pg_trgm's `%` (default threshold 0.3, GIN index from migration 0014) and that were first seen within 30 days of it. The search finds every duplicate pair of the test sample. The run lock is a transaction-level advisory lock (`locks.CLUSTER`), and a busy run does nothing.
- **The message shows the most complete vacancy** of the cluster (salary, then company, then lowest id) among those that match one of the delivery's subscriptions, picked when the delivery is claimed (found in review: the fullest vacancy may state a format the subscription rules out).
- **Matching's lookback counts from each vacancy, the watermark from its cluster** (found in review). A vacancy that joins a cluster more than 2 days after its first posting is matched on its own facts, so "any vacancy of a cluster matches" holds; a job that appeared before a subscription is still never sent to it, and the unique delivery keeps it from being sent twice. In the sample, 11 of 79 later postings arrived more than 2 days after their cluster's first one (longest gap 91 hours), 6 of them from the other site and 2 of those with another work format.
- **Runs of at most 1000 vacancies, each committed on its own,** so a rebuild from raw data does not hold the lock for one long transaction. A run reads each vacancy's facts once. `scripts/dedup_sample.py check` runs the real code on a copy of a backup and compares it with the measurement.
- **The 30-day candidate window is an assumption.** The four-day sample cannot show how far apart reposts are; the findings PR measures it on the longer data.
- **No exact fingerprint and no rapidfuzz.** DOU never reposted a vacancy with the same company and title in the sample, and Djinni has neither. Set operations on shingle hashes need no new dependency.
- **The description is stored** as `vacancy.description` (#33, `NORMALIZER_VERSION` 3); stage 9 search reads it too.
- **Test pairs keep no text.** DOU's consent rules out publishing full descriptions, so `tests/fixtures/dedup/pairs.json` keeps feed titles and the descriptions' shingle hashes, made with a random key that was thrown away (plain hashes can be turned back into text word by word, found in review). `scripts/dedup_sample.py` measures the rule on a backup and rebuilds the fixture.
- **Numbers and symbols stay in titles.** "C#", "C++" and "C" are different words, and so are "Level 1" and "Level 2" (found in review); an internal number added on one site only is covered by the subset rule.

### Stage 9: Search and analytics
- [ ] FTS `search_vector` (`simple` + `unaccent`), GIN on it and on `skills` (the trigram index on `title` exists since stage 8: `ix_vacancy_title_trgm`)
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
