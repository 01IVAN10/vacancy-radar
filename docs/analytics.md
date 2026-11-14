# Analytics

Market aggregates for the `/analytics` page: top skills per month, salary ranges per week, and new jobs per week. They are materialized views (migration 0017), refreshed every half hour, so a page view reads a few small tables instead of aggregating every vacancy.

## What is counted

**Jobs, not postings.** A job posted on Djinni and DOU, or reposted, is one cluster (`docs/deduplication.md`) and counts once. `analytics_job` has one row per job, made from all its postings together:

- **Skills and levels** are those any of its postings names. A Djinni posting may name skills that the DOU one leaves out, and the reverse.
- **The salary** is the monthly dollar salary of its first posting that has one. A posting with an hourly rate or an unconverted salary has none, so the next one's counts.

No posting "shows" the job here. The search page and Telegram show one posting of a job (`clusters/showing.py`), and analytics never has to agree with how that one is picked (found in review: picking one posting gave a job the levels of a posting other than the one shown). Vacancies without a cluster yet are not counted.

**When.** A job belongs to the Kyiv calendar day, week (Monday first) and month in which it was first seen: its cluster's `first_seen_at`, the earliest sighting of any of its postings. So a repost never counts as a new job, and a rebuild from raw data puts every job back where it was.

**On each source,** a job counts in the week it first appeared on that source (`analytics_job_source`), not in the week it first appeared anywhere. A posting can join a cluster up to 30 days after its twin on the other site (`CANDIDATE_WINDOW`), and dated by the cluster it would add to a week that is already over (found in review). On the copy of 2026-11-14 this moved 14 DOU jobs from the week of 2 November to the week of 9 November, where they appeared on DOU.

**Jobs that name a skill.** Djinni's feed has every job, sales, HR and accounting included. On the copy of 2026-11-14, 2428 of 4046 jobs name a skill from the dictionary (`domain/skills.py`). Skill shares and salaries count only those, so the salaries of sales managers do not stand in for the IT market. The dictionary also has a few office skills (Excel, Jira), so this set is close to, but not exactly, "IT jobs". Weekly job counts show both numbers.

## The views

| View | Rows | Columns |
|---|---|---|
| `analytics_job` | one per job | `first_seen_on`, `postings`, `skills`, `seniority`, `salary_usd` |
| `analytics_job_source` | one per job and source | `first_seen_on` on that source |
| `analytics_skill_monthly` | one per month and skill | `n`: jobs that name the skill; `jobs`: jobs that name any skill that month, the base of the share |
| `analytics_salary_weekly` | one per week and level, and per week for `all` levels | `n`, `p25`, `median`, `p75` of monthly salaries in US dollars |
| `analytics_jobs_weekly` | one per week and source, and per week for `all` sources | `jobs`; `with_skills`: those that name a skill (the job, through any posting) |
| `analytics_summary` | one | `refreshed_at`, `jobs`, `postings`, `with_skills`, `with_salary`, `first_day`, `last_day` |

- **A salary** is the middle of the job's monthly dollar range, or its one bound when only one is stated ("from $3000"). Hourly rates have no monthly value (`docs/normalization.md`) and are left out. Quartiles are `percentile_cont`, rounded to whole dollars.
- **Levels.** A job that names several levels ("Middle/Senior") counts in each of them and once in `all`.
- **Sources.** A job posted on both sites counts in each source's series and once in `all`, so the sources add up to more than `all`.
- **`all`** marks both the series of every level and of every source (`domain.analytics.ALL`).

## Showing them honestly

The rules are `domain/analytics.py`. The views keep every number; `analytics/service.py:read_analytics`, the one function the page calls, applies the rules at the moment it is given.

- **Every aggregate shows its sample size `n`.**
- **A salary median of fewer than 10 jobs (`MIN_SAMPLE`) is not shown at all,** neither on the page nor in the data the page sends, only its `n`. A week of 2026-11-09 to 11-15 had 188 jobs with a dollar salary in all, but only 3 to 13 per level, so per-level weeks fall on both sides of the line, and the `all` series is the one to read week by week.
- **Incomplete periods are marked.** Collection started on Thursday 2026-11-05, so the first week and month are incomplete, and so is the current one; their counts are lower for that reason alone. A week is incomplete until its Sunday has ended in Kyiv, and a month until its last day has (found in review: the first version called a week complete on its Sunday). `read_analytics` takes the current moment and works out the Kyiv day itself, so a server running in UTC cannot shift the line.
- **Salaries are rare.** Only about one job in ten that names a skill states a salary, and those that do are not a random sample of the market.

## Refresh

`analytics.refresh` (`analytics/refresh.py`) runs `REFRESH MATERIALIZED VIEW CONCURRENTLY` on each view in dependency order, the views of jobs first, all in one transaction, and commits. A concurrent refresh builds the new rows next to the old ones and swaps in what changed, so pages keep reading the old rows meanwhile; it needs the unique index each view has. Because it is one transaction, readers see all the old views or all the new ones. Two refreshes of one view wait for each other, so no lock of our own is needed, and a second run only repeats the work. The task has no retries: a failed refresh leaves the old rows, and the next one replaces them.

| Task | Command |
|---|---|
| Refresh now | `uv run python -m vacancy_radar.analytics` |
| Run the task in Compose | `docker compose exec worker celery -A vacancy_radar.worker call analytics.refresh` |

Beat sends `analytics.refresh` every 30 minutes (`worker.py`). Measured on 2026-11-15 on the copies described in `docs/search.md`, Measurements:

| Data | Jobs | Refresh |
|---|---|---|
| copy of the hosted database, 2026-11-14 | 4046 | 95 ms |
| the same ×50, about a year | 202 300 | 2.4 s |

At ×50 the aggregates take 24–208 kB each, and the two views of jobs 28 MB and 17 MB. Materializing the views of jobs doubled the refresh time against a plain view, but each aggregate now reads them instead of sorting every vacancy again.

The views read `vacancy` columns, so a migration that changes the type of one of them has to drop and recreate the views (migration 0017 has their definitions). Tests that commit empty the views again after `TRUNCATE` (`tests/support.py:empty_database`), in dependency order through `refresh_views`.
