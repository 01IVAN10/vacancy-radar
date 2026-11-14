# Analytics

Market aggregates for the `/analytics` page: top skills per month, salary ranges per week, and new jobs per week. They are materialized views (migration 0017), refreshed every half hour, so a page view reads a few small tables instead of aggregating every vacancy.

## What is counted

**Jobs, not postings.** A job posted on Djinni and DOU, or reposted, is one cluster (`docs/deduplication.md`) and counts once. `analytics_job` has one row per job, made from all its postings together:

- **Skills and levels** are those any of its postings names. A Djinni posting may name skills that the DOU one leaves out, and the reverse.
- **The salary** is the monthly dollar salary of its first posting that has one. A posting with an hourly rate or an unconverted salary has none, so the next one's counts.

No posting "shows" the job here. The search page and Telegram show one posting of a job (`clusters/showing.py`), and analytics never has to agree with how that one is picked (found in review: picking one posting gave a job the levels of a posting other than the one shown). Vacancies without a cluster yet are not counted.

**When.** A job belongs to the Kyiv calendar day, week (Monday first) and month in which it was first seen: its cluster's `first_seen_at`, the earliest sighting of any of its postings. So a repost never counts as a new job, and a rebuild from raw data puts every job back where it was.

**On each source,** a job counts in the week it first appeared on that source (`analytics_job_source`), not in the week it first appeared anywhere. A posting can join a cluster up to 30 days after its twin on the other site (`CANDIDATE_WINDOW`), and dated by the cluster it would add to a week that is already over (found in review). On the copy of 2026-11-14 this moved 14 DOU jobs from the week of 2 November to the week of 9 November, where they appeared on DOU. So job counts are final once a week is over; skills and salaries are not yet (below, Settling).

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
- **Settling periods are marked too.** A job's twin on the other site can join it up to 30 days after the job first appeared (`CANDIDATE_WINDOW`), and brings its skills, levels and salary. So the skill shares, the salaries and the jobs that name a skill of a period that is over can still change until 30 days after it ended (found in review: a Djinni posting without a salary on 3 November and its DOU twin with $4000 on 17 November add a salary to the week of 2 November). On the 2026-11-09 sample, 67 jobs were posted on both sites. `Coverage.week_is_settling` and `month_is_settling` mark such periods, and the page says their numbers are not final. Job counts are final as soon as a period is over.
- **Totals are kept apart.** The views mark the total of every level or source with `all`; `read_analytics` returns the total as `jobs_total` and the sources as `jobs_by_source`, and takes a level as `Level | None`, so a chart of sources cannot count a job on two sites three times (found in review).
- **Salaries are rare.** Only about one job in ten that names a skill states a salary, and those that do are not a random sample of the market.

## The page

`GET /analytics` (`api/analytics.py`) is public. It shows what the numbers cover (jobs, postings, the first day, when the views were refreshed, the share of jobs with a salary), credits each source with a link (a condition of DOU's consent, `docs/sources.md`), and draws three charts:

| Chart | Choice | Shown |
|---|---|---|
| Top skills of a month | `month=2026-11`, the newest by default | the 15 most named skills, each "n of N jobs" and as a share |
| Salaries by week | `level=senior`, every level by default | median and the 25th to 75th percentile band, with `n`; a week below `MIN_SAMPLE` is a gap in the chart and "hidden" in the table |
| New jobs by week | none | all sources, and each source, with the jobs that name a skill |

- **Every chart has a table** with the same numbers under it, so the page reads without JavaScript and with a screen reader.
- **No inline script.** The numbers are a JSON block (`<script type="application/json">`), and `static/analytics.js` draws them with Chart.js from a CDN, pinned with SRI like Pico and HTMX. So the Content Security Policy of stage 11 can forbid inline scripts. Jinja's `tojson` escapes `<`, `>` and `&`, so a value such as `</script>` cannot end the block.
- **A hidden median is not sent at all**: its quartiles are `null` in the JSON and absent from the table, and only its `n` is shown.
- **The choices are a plain GET form sent by its button,** so a chart is a link, and Back restores the page and the form. It is not sent when a choice changes: a closed select changes on every arrow key, so a keyboard user would reload the page at each step (found in review; WCAG 3.2.2). No HTMX: the page is not swapped in parts, so the charts never have to be drawn again after a swap.
- **The choices are checked in `domain/analytics.py`** (`build_analytics_query`): an unknown month or level is a 422 with a message. Every level together is the empty choice, never the views' word `all`. `read_analytics` returns the month it shows (`Analytics.month`) as it returns the level, and the page takes both from there. A valid month without data shows that there is none, and the form offers that month too, so the heading and the form agree and sending the form again keeps it (found in review: the form showed the newest month and moved the page to it).
- **The JSON block is typed** (`ChartData` in `api/analytics.py`): every list of a chart is a column of its table, built from the same rows, so mypy checks what `static/analytics.js` reads. A series of new jobs carries a key (`total` or a source slug), and the script picks its colour by that key, so a new source cannot repaint Djinni and DOU.
- **Sources come from the `source` table** through `db/sources.py`, the one reader that search uses too, so both pages offer the same sites in the same order.
- **Incomplete periods carry an asterisk** on the charts and in the tables (one function, `week_label`), and the captions say from which week the numbers are not final.
- **Dates are spelled out in English** (`api/templating.py`), not with `strftime`, whose month names follow the process locale.

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

The views read `vacancy` columns, so a migration that changes the type of one of them has to drop and recreate the views (migration 0017 has their definitions). A test checks that every `analytics*` materialized view in the database is in `MATERIALIZED_VIEWS` with the columns the code reads (`tests/analytics/test_views.py`), so a view a migration adds cannot be left out of the refresh. Tests that commit empty the views again after `TRUNCATE` (`tests/support.py:empty_database`), in dependency order through `refresh_views`.

## Measurements

Measured on 2026-11-15 on the copies described in `docs/search.md`, Measurements: the hosted database of 2026-11-14 (4405 postings in 4046 jobs) and the same ×50 (202 300 jobs, about a year), Postgres 16 in Docker on a MacBook. Times are the median planning plus execution time of five `EXPLAIN (ANALYZE, BUFFERS)` runs after one warm-up. "Live" runs the definitions of the views as one query, with `analytics_job` and `analytics_job_source` as CTEs: what the page would cost without materialized views.

| Read | Today, page | Today, live | ×50, page | ×50, live |
|---|---|---|---|---|
| top skills of the newest month | 0.03 ms | 16 ms | 0.02 ms | 555 ms |
| weekly salaries, every level | 0.01 ms | 11 ms | 0.03 ms | 647 ms |
| new jobs per week and source | 0.01 ms | 19 ms | 0.04 ms | 1554 ms |
| everything the page reads (summary, months, sources too) | 0.1 ms | | 0.3 ms | |

- **The page costs nothing to read.** Its six statements read a few hundred rows of small views, 0.1 ms today and 0.3 ms a year from now; the whole response takes about 4 ms locally, most of it rendering. Computed live, the same charts would take 46 ms today and 2.8 seconds at ×50, on every view of a public page.
- **The cost moved into the refresh,** 95 ms today and 2.4 s at ×50 (Refresh, above), paid once every half hour instead of once per visitor.

### What the copy shows

From the copy of 2026-11-14: nine days of collection, the week of 2 November from Thursday only, and the week of 9 November without its Sunday. Every number is partial and not final, and two weeks are too short for a trend.

- **Top skills of November:** Python in 25% of the 2428 jobs that name a skill, SQL 21%, CI/CD 18%, then Excel, AWS and Jira at 14%. Excel and Jira near the top are the office roles of Djinni's feed (below), not a finding about developers.
- **Salaries are rare and skewed.** 475 of 4046 jobs state a monthly dollar salary, and 255 of them name no skill: sales, support and other non-IT roles state a salary more often than developers. Among jobs that name a skill, 9.1% state one (220).
- **Per level, weeks are too small.** The week of 9 November had 188 salaries in all, but only 3 to 13 per level, so most per-level medians are hidden. Most jobs with a salary name no level at all: the levels add up to 52 of the 188.
- **The all-level median was $1500 in the week of 9 November** (middle half $886 to $2175). The levels just above the line were junior $820, middle $1750 and senior $2000 (n = 12–13 each), so a single week of one level is a handful of jobs. Read these as "the jobs that chose to state a salary", not as "the market".
- **Djinni's feed has every role.** 1618 of 4046 jobs (40%) name no skill from the dictionary: sales, HR, accounting. They count in "New jobs by week" but not in skills and salaries.
- **Sources overlap.** In the week of 9 November, Djinni had 2188 jobs and DOU 1407, together 3335: 260 jobs were on both.
