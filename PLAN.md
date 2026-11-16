# Vacancy Radar: roadmap

Each stage is one or more PRs (`feat/<name>` → `main`). Tick boxes in the PR that completes them.

Order follows a vertical slice: start accumulating raw data as early as possible (RSS keeps no history), then build a working version for personal use, then the showcase features and production deploy.

## Current state

Updated 2026-11-16.

- **Stage 3** is done. Djinni and DOU are collected into Neon, started every 15 minutes by cron-job.org because GitHub drops scheduled runs (see `docs/collection.md`). A week of runs (2026-11-06 to 11-12) missed no items; Djinni is fetched every 30 minutes with a wide margin, DOU mostly every 45 with a thin one at the weekday peak (`docs/collection.md`, Measured). Since `fix/dou-due-wait` the collector waits up to 2 minutes for a source that is almost due, and DOU is fetched every 30 minutes: all 53 gaps in the first 27 hours were 30:01–30:02 (`docs/collection.md`, Measured after the wait). A burst of more than about 50 DOU postings in half an hour can still push items out of the feed; on 2026-11-13 a burst of 54 left a margin of 3.
- **Stage 4** is done: Celery collection with Beat, retries, and a Redis rate limiter. It runs locally in Compose for Djinni only, and in production from stage 11, when it replaces the GitHub Actions job.
- **Stage 5** is done: `raw_vacancy` is normalized into `vacancy` (salary in its currency and in monthly USD, seniority, work format, skills, DOU company and places) by `python -m vacancy_radar.normalize` and the Celery task `normalize.pending`. See `docs/normalization.md`. It runs locally only. Neon keeps accumulating raw data, and vacancies are rebuilt from it on the VPS (stage 11).
- **Stage 6** is done: accounts with sign-in and CSRF protection (`docs/auth.md`), and subscription pages at `/subscriptions` (`docs/subscriptions.md`).
- **Stage 7** is done: subscription matching (#24), Telegram linking (#25) and notifications. `notify.match` gives each vacancy a cluster of its own and records the matches in `notification`, unique on `(subscription_id, cluster_id)`, with one `delivery` per account and cluster; `notify.send` sends each delivery at most once, with 429 handling (`docs/telegram.md`, Delivery). The bot's restart behaviour and delivery end to end were checked by hand on 2026-11-08: a Djinni run brought 46 new vacancies, 14 matched, and each arrived in Telegram once.
- **Problems on the way:** the review found that unknown seniority, format and salary let a one-criterion subscription match almost every vacancy, and that the bot re-answered its last batch after every restart. Both are fixed. A salary not yet converted from UAH would have read as no salary, so matching waits up to an hour for conversion. A request that timed out after it was sent may have been delivered, so it is not retried.
- **Before stage 8:** the architecture review of `main` (2026-11-08) is done. Its prep PRs are merged: a CI gate for `domain/` coverage ≥ 90% (#28), clustering in its own package `clusters/` with notify tests independent of it (#29), and the stage 8 rules below.
- **Stage 8** is done: vacancies keep their plain description (#33), the same-job rule is measured on real data (#34, `domain/dedup.py`), and `notify.match` puts each new vacancy into the cluster of the same job (`clusters/service.py`, `docs/deduplication.md`). On the 2026-11-09 sample, 77 jobs were posted twice or three times, 67 of them on both Djinni and DOU. It runs locally only; Neon keeps collecting raw data, and the VPS clusters it in stage 11.
- **Problems on the way (stage 8):** the first test fixture stored plain shingle hashes, which can be turned back into DOU descriptions word by word; it now uses hashes made with a key that was thrown away, the branch was force-pushed, and GitHub Support is asked to purge the old commit. Title words dropped `#`, `+` and numbers, so "C#" and "C++", or "Level 1" and "Level 2", looked like one job; symbols and numbers now stay. Matching's lookback counted from the cluster, so a duplicate that arrived more than 2 days later was never matched on its own facts; it now counts from each vacancy. Docker Desktop containers had no internet while the Mac was on a phone hotspot.
- **Problems on the way (findings, 2026-11-13):** GitHub's backup `schedule` started 25 runs in six days against cron-job.org's 615, so the job starts every 15 minutes, and DOU's 30-minute `min_interval` turns into 45 minutes whenever a start comes a few seconds early. Deduplication now depends on arrival order in two cases where one posting matches two twins the rule keeps apart; no different jobs are merged either way (`docs/deduplication.md`, Arrival order). `scripts/dedup_sample.py` split COPY rows on U+2028 inside descriptions.
- **Stage 9** is done: full-text search at `/vacancies` (#40, #41, `docs/search.md`) and market analytics at `/analytics` from materialized views refreshed every half hour (#42, #43, `docs/analytics.md`). It runs locally only; Neon is at migration 0017 with empty views, because it holds raw data only, and the VPS builds them in stage 11.
- **Problems on the way (stage 9):** a query like `-c/c++` gave a contradictory tsquery, so a word with a slash is now a phrase. The week was called complete on its Sunday, while that Sunday was still being collected. The rule for the posting shown disagreed between search and analytics, so analytics now uses every posting of a job. Late twins rewrote past source weeks, so each source counts a job in the week it appeared there; they still change skills and salaries for 30 days, which the page marks. Pico laid out the whole `role=search` form as one row. HTMX's history snapshot reset the search form on Back. HTMX silently drops a request with an invalid field unless `reportValidityOfForms` is on. `</script>` swallowed text after the first slash fix. On `/analytics`, a month asked for without data was shown in the heading while the form fell back to the newest month; the form now offers that month too. The form was sent on every change, so a keyboard user moving through the options was taken to a new page (WCAG 3.2.2); it is now sent by its button only.
- **Stage 10** is done: deduplication across Djinni and DOU is checked against 433 hand-labelled pairs from the Neon backup of 2026-11-15 (`scripts/dedup_sample.py audit`, #45). The stage 8 rule made no wrong merge but joined 301 of 370 duplicate pairs; most misses were one text with a company introduction added on one site. Across sources, a description that is 90% inside the other now counts too (containment), which joins 327 of 370 with no wrong merge (`docs/deduplication.md`). It runs locally only; the VPS clusters Neon's raw data in stage 11.
- **Problems on the way (stage 10):** the review of #45 found that the audit let its own "plainly the same" shortcut override hand labels, and that looking for pairs through rare shingles missed 4 close pairs; labels now win and pairs are found by prefix filtering, which loses none. Since migration 0015 Neon's schema dump carries `pg_attver` column options that a plain Postgres rejects, so the check builds the schema from migrations and loads only the raw items. SKELAR's "Product Manager", a non-duplicate of the stage 8 fixture, turned out to be one job: the whole Djinni text is inside DOU's.
- **Before stage 11:** the architecture review of `main` (2026-11-15) is done. Its prep PRs are merged: the image carries the migrations and Compose applies them with a one-shot `migrate` service, so `docker compose up --build` gives a working site on an empty database (#54); a short `SECRET_KEY` is rejected, cookies are `Secure` when `PUBLIC_BASE_URL` is https, and the web app, worker and bot refuse to start in production without an https site URL or with a development key (#55); README and docstrings match the code (#56); retry backoff is one module, and only `worker.py` and the task modules use Celery (#57). The rest is in issues #47–#53, with the inputs for stage 11 in #53.
- **Stage 11 is deferred** (2026-11-16): there is no budget for hosting yet. Collection into Neon keeps running, so raw data keeps accumulating for a later deploy. Everything else runs locally, where `docker compose up --build` starts the full stack. The project is finished as a portfolio project without a production deploy; the MVP definition of done in `CLAUDE.md` is reworded to match.
- **Stage 12** is done: CI and coverage badges (#64), and a README with architecture diagrams, decisions and trade-offs, the deferred deploy, screenshots and a demo video (#65, #67). The MVP definition of done was checked on 2026-11-16 (#67): collection into Neon runs every 15 minutes; a fresh clone with no `.env` came up with `docker compose up --build`, and its first 100 Djinni jobs were searchable after about 15 minutes; deduplication and `/analytics` work on the Neon backup of 2026-11-15 (4439 postings, 4051 jobs); 48 matches reached Telegram once each, and restarting the worker and the bot and running `notify.send` again sent none twice; CI is green with `domain/` coverage at 100%. Found on the way: the home page search button wrapped onto two lines (#66).
- **The project is complete** as a portfolio project without a production deploy. Stage 11 stays deferred until there is a hosting budget; issues #48–#51 and #53 stay open for it and for later refactoring.

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
- [x] Verify data arrives over several days

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
- **The 30-day candidate window is an assumption.** The four-day sample cannot show how far apart reposts are. (2026-11-13: the longest gap grew to 159 hours in seven days, so it stays an assumption until a month of data can be measured in stage 11.)
- **No exact fingerprint and no rapidfuzz.** DOU never reposted a vacancy with the same company and title in the sample, and Djinni has neither. Set operations on shingle hashes need no new dependency.
- **The description is stored** as `vacancy.description` (#33, `NORMALIZER_VERSION` 3); stage 9 search reads it too.
- **Test pairs keep no text.** DOU's consent rules out publishing full descriptions, so `tests/fixtures/dedup/pairs.json` keeps feed titles and the descriptions' shingle hashes, made with a random key that was thrown away (plain hashes can be turned back into text word by word, found in review). `scripts/dedup_sample.py` measures the rule on a backup and rebuilds the fixture.
- **Numbers and symbols stay in titles.** "C#", "C++" and "C" are different words, and so are "Level 1" and "Level 2" (found in review); an internal number added on one site only is covered by the subset rule.

### Stage 9: Search and analytics
- [x] FTS `search_vector` (`simple` + `unaccent`), GIN on it and on `skills` (the trigram index on `title` exists since stage 8: `ix_vacancy_title_trgm`; see `docs/search.md`)
- [x] Vacancy search page: FTS + filters + pagination (`/vacancies`, one result per job)
- [x] Materialized views: top skills per month, salary ranges per week, new vacancies per week (each with `n`; jobs, not postings; `docs/analytics.md`)
- [x] Periodic refresh task (`analytics.refresh`, Beat every 30 min, CLI `python -m vacancy_radar.analytics`)
- [x] `/analytics` page with Chart.js, medians hidden below the minimum sample
- [x] `docs/search.md`: queries, EXPLAIN ANALYZE before/after indexes (today and ×50), findings
- [x] `docs/analytics.md`: queries, EXPLAIN ANALYZE, findings

Decisions (2026-11-15):
- **Search** reads the vacancies through `search_vector` (`simple` + `unaccent`) and shows each job once, by the same posting as Telegram (`docs/search.md`). Its form is checked in `domain/search.py`, like the subscription form.
- **Analytics counts jobs, not postings,** from materialized views refreshed every 30 minutes (`docs/analytics.md`). A job counts on each source in the week it appeared there. Medians of fewer than 10 salaries are not sent at all, and every period is marked partial or settling (30 days, `CANDIDATE_WINDOW`).
- **The analytics page has no inline script and no HTMX:** the numbers are a JSON block that `static/analytics.js` draws, each chart has a table, and its choices are a plain GET form. The page reads the views in 0.1 ms today and 0.3 ms at ×50, against 46 ms and 2.8 s computed live.

### Stage 10: Second source
- [x] Written consent from DOU recorded in `docs/sources.md` (request to `legal@dou.ua`)
- [x] Adapter for the second allowed source (DOU once consented, per `docs/sources.md`) + its fixtures (done early, in stage 3)
- [x] Cross-source dedup verified on real data (433 labelled pairs, containment across sources; `docs/deduplication.md`)

Decisions (2026-11-15):
- **Verified on a copy of the backup, not live.** Precision and recall come from hand labels of every Djinni × DOU pair whose texts share at least 0.5 (Jaccard) or 0.75 of the shorter one (containment). The real `normalize_pending` and `assign_clusters` give the same clusters on a copy of the backup (`check`). A job posted on both sites giving one cluster and one Telegram message is part of the stage 11 smoke check.
- **Containment across sources only.** Two descriptions from different sources are also the same job when 90% of the shorter one's shingles are in the other (`CROSS_SOURCE_CONTAINMENT`), because one site often wraps the same text in a company introduction or benefits. Within one source only Jaccard 0.85 counts: the labels cover pairs across sources only. The margin is thin: two roles of one company template reach 0.83 and are kept apart only by their titles, and Djinni's feed has no company or places to veto with. A minimum text size for containment (100 shingles) would cost 2 of 28 pairs and stop nothing seen, so it is held back until a wrong merge turns up. The author chose it over Jaccard 0.75 across sources (13 more pairs instead of 26), and decided the three unsure pairs it turned on (Sigma Software, SKELAR, Applyft: one job each).
- **The vetoes and the title rule stay.** They miss 26 duplicate pairs (synonyms and translations of titles, a level named on one site only), but keep apart 51 pairs of different roles of one company that share their text.

### Stage 11: Deploy to VPS (deferred)

Deferred on 2026-11-16 because there is no budget for hosting yet; the items below stay as the plan for when there is. Until then, cron-job.org starts the `Collect` workflow every 15 minutes and it collects Djinni and DOU into Neon, and the rest of the stack runs locally. Already done for the deploy: the image carries the migrations and a one-shot `migrate` service applies them (#54), production settings refuse a development key or a non-https site URL and make cookies `Secure` (#55), the API logs JSON, hides `/docs` in production and has no inline styles, so a CSP can forbid them (#62), and CI builds the image and smoke-tests `/health` in it. The rest of #48 and the inputs in #53 stay open for this stage.

- [ ] Production Compose file + Caddy (HTTPS)
- [ ] VPS setup: Docker, firewall, non-root user; Postgres and Redis not exposed, Redis password
- [ ] Nightly `pg_dump` backup + tested restore
- [ ] GitHub Actions deploy over SSH, secrets in GH Secrets
- [ ] Migrate data from the free Postgres (`pg_dump` → `pg_restore`), disable the cron workflow. Neon's schema dump carries `pg_attver` column options a plain Postgres rejects: build the schema with migrations and restore data only (as `scripts/dedup_sample.py check` does)
- [ ] Delete the cron-job.org job and revoke its token
- [ ] Rate-limit sign-in, sign-up and the public search (Redis token bucket), CSP and HSTS in Caddy
- [ ] Postgres tuned for SSD: `random_page_cost = 1.1`, so a common word uses the search index (`docs/search.md`, Measurements)
- [ ] Smoke check after deploy, including one job posted on Djinni and DOU: one cluster, one Telegram message

### Stage 12: Polish
- [x] README: architecture diagram, screenshots, quickstart, decisions and trade-offs
- [x] CI and coverage badges
- [x] Verify the MVP definition of done in `CLAUDE.md`

## Deferred (post-MVP)
Email notifications, Work.ua and other sources, Jupyter notebook with market research, admin panel, ML seniority classification, scheduled digests, Sentry/Prometheus, UI i18n.
