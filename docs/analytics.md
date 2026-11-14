# Analytics

Market aggregates for the `/analytics` page: top skills per month, salary ranges per week, and new jobs per week. They are materialized views (migration 0017), refreshed every half hour, so a page view reads a few small tables instead of aggregating every vacancy.

## What is counted

**Jobs, not postings.** A job posted on Djinni and DOU, or reposted, is one cluster (`docs/deduplication.md`) and counts once. `analytics_job`, a plain view, has one row per job, taken from the vacancy that shows it:

1. one with a monthly salary in US dollars;
2. then the fullest (a salary in any currency, then a company);
3. then the lowest id.

Vacancies without a cluster yet are not counted.

**When.** A job belongs to the Kyiv calendar day, week (Monday first) and month in which it was first seen: its cluster's `first_seen_at`, the earliest sighting of any of its postings. So a repost never counts as a new job, and a rebuild from raw data puts every job back where it was.

**Jobs that name a skill.** Djinni's feed has every job, sales, HR and accounting included. On the copy of 2026-11-14, 2428 of 4046 jobs name a skill from the dictionary (`domain/skills.py`). Skill shares and salaries count only those, so the salaries of sales managers do not stand in for the IT market. The dictionary also has a few office skills (Excel, Jira), so this set is close to, but not exactly, "IT jobs". Weekly job counts show both numbers.

## The views

| View | Rows | Columns |
|---|---|---|
| `analytics_skill_monthly` | one per month and skill | `n`: jobs that name the skill; `jobs`: jobs that name any skill that month, the base of the share |
| `analytics_salary_weekly` | one per week and level, and per week for `all` levels | `n`, `p25`, `median`, `p75` of monthly salaries in US dollars |
| `analytics_jobs_weekly` | one per week and source, and per week for `all` sources | `jobs`; `with_skills`: those that name a skill |
| `analytics_summary` | one | `refreshed_at`, `jobs`, `postings`, `with_skills`, `with_salary`, `first_day`, `last_day` |

- **A salary** is the middle of the job's monthly dollar range, or its one bound when only one is stated ("from $3000"). Hourly rates have no monthly value (`docs/normalization.md`) and are left out. Quartiles are `percentile_cont`, rounded to whole dollars.
- **Levels.** A job that names several levels ("Middle/Senior") counts in each of them and once in `all`.
- **Sources.** A job posted on both sites counts in each source's series and once in `all`, so the sources add up to more than `all`.

## Showing them honestly

The rules are `domain/analytics.py`. The page applies them, and the views keep every number.

- **Every aggregate shows its sample size `n`.**
- **A salary median of fewer than 10 jobs (`MIN_SAMPLE`) is not shown at all,** neither on the page nor in the data the page sends, only its `n`. A week of 2026-11-09 to 11-15 had 188 jobs with a dollar salary in all, but only 3 to 13 per level, so per-level weeks fall on both sides of the line, and the `all` series is the one to read week by week.
- **Incomplete periods are marked.** Collection started on Thursday 2026-11-05, so the first week and month are incomplete, and so is the current one; their counts are lower for that reason alone.
- **Salaries are rare.** Only about one job in ten that names a skill states a salary, and those that do are not a random sample of the market.

## Refresh

`analytics.refresh` (`analytics/refresh.py`) runs `REFRESH MATERIALIZED VIEW CONCURRENTLY` on each view, the summary last, and commits. A concurrent refresh builds the new rows next to the old ones and swaps in what changed, so pages keep reading the old rows meanwhile; it needs the unique index each view has. Two refreshes of one view wait for each other, so no lock of our own is needed, and a second run only repeats the work. The task has no retries: a failed refresh leaves the old rows, and the next one replaces them.

| Task | Command |
|---|---|
| Refresh now | `uv run python -m vacancy_radar.analytics` |
| Run the task in Compose | `docker compose exec worker celery -A vacancy_radar.worker call analytics.refresh` |

Beat sends `analytics.refresh` every 30 minutes (`worker.py`). Measured on 2026-11-15 on the copies described in `docs/search.md`, Measurements:

| Data | Jobs | Refresh |
|---|---|---|
| copy of the hosted database, 2026-11-14 | 4046 | 50 ms |
| the same ×50, about a year | 202 300 | 1.2 s |

At ×50 the views take 24–208 kB each.

The views read `vacancy` columns, so a migration that changes the type of one of them has to drop and recreate the views (migration 0017 has their definitions). Tests that commit empty the views again after `TRUNCATE` (`tests/conftest.py`).
