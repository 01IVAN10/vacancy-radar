# Checking collection

Every request to a source is a row in `collect_run`. The row is committed as `running` before the request is sent, then finished as `ok`, `failed`, or `dry_run` (fetched without storing items). Any error after the request, a database error included, finishes the run as `failed`. A run that stays `running` was interrupted (the process died, or the database could not record the failure), and it still counts as a request for the source's `min_interval`. These questions are answered with SQL instead of job logs.

- Did collection run on schedule?
- Did it fail?
- Did the feed move past items between two runs?

Run the queries with `psql` against the hosted database, or through Docker without a local client:

```sh
docker run --rm -i -e PGURL="$(sed -n 's/^DATABASE_URL=//p' .env.neon | sed 's/+psycopg//')" \
  postgres:16 sh -c 'psql "$PGURL"' < query.sql
```

## Triggers

The workflow has two triggers. cron-job.org calls the `workflow_dispatch` API at minutes 7, 22, 37, and 52 of every hour, and GitHub's `schedule` fires at minutes 2, 17, 32, and 47 as a backup. Setup is in the README, under [Trigger](../README.md#trigger). `gh run list --workflow collect.yml` shows which trigger started each run in its event column.

GitHub's `schedule` alone was not enough. From the first run on 2026-11-05 22:08 UTC to 2026-11-06 12:09 UTC, about 56 scheduled slots passed and only 2 runs started:

| Run (UTC) | Trigger | Djinni new / already stored | DOU new / already stored | Gap |
|---|---|---|---|---|
| 11-05 22:08 / 22:21 | manual | 2 / 98 | 50 / 0 (first run) | |
| 11-06 02:40 | schedule | 2 / 98 | 1 / 49 | ~4.5 h |
| 11-06 09:53 | schedule | 90 / 10 | **50 / 0** | 7 h 13 min |
| 11-06 12:33 | first cron-job.org dispatch | 89 / 11 | **50 / 0** | 2 h 40 min |

The DOU runs at 09:53 and 12:33 shared no items with the previous run, so the feed moved past vacancies that were never stored. Even a daytime gap of under 3 hours is too long for DOU. Djinni overlapped by only 10–11 items, so it was close to missing items too.

The backup trigger is still unreliable: from 2026-11-06 12:33 to 2026-11-12 22:00 UTC, cron-job.org started 615 runs and GitHub's `schedule` 25. So in practice the job starts every 15 minutes, at cron-job.org's minutes (see [Measured](#measured-2026-11-06-to-2026-11-12)).

## Runs per day

Each source is fetched only when its `min_interval` has passed since its last request. Djinni (25 minutes) is due at every second start of the job, so expect 48 `ok` runs per day. DOU (30 minutes) is due at the second start only when that start comes at least 30 minutes after the previous request. The job starts a few seconds earlier or later each time, so DOU is fetched every 30 or 45 minutes: 35–38 runs per day were measured. Runs skipped as not due leave no row.

```sql
SELECT s.slug,
       date_trunc('day', r.started_at AT TIME ZONE 'Europe/Kyiv') AS day,
       count(*) FILTER (WHERE r.status = 'ok')     AS ok,
       count(*) FILTER (WHERE r.status = 'failed') AS failed,
       count(*) FILTER (WHERE r.status = 'running') AS running,
       sum(r.inserted)                             AS new_items
FROM collect_run r
JOIN source s ON s.id = r.source_id
GROUP BY 1, 2
ORDER BY 1, 2;
```

## Gaps between runs

GitHub delays scheduled jobs under load. A gap longer than half the feed's window at the busiest hour (about 50 minutes for Djinni and 45 for DOU, see [Feed window](#feed-window)) risks missed items.

```sql
SELECT slug,
       percentile_cont(ARRAY[0.5, 0.9, 0.99]) WITHIN GROUP (ORDER BY gap) AS gap_p50_p90_p99,
       max(gap) AS max_gap
FROM (
    SELECT s.slug,
           r.started_at - lag(r.started_at) OVER (PARTITION BY r.source_id ORDER BY r.started_at)
               AS gap
    FROM collect_run r
    JOIN source s ON s.id = r.source_id
) gaps
WHERE gap IS NOT NULL
GROUP BY slug;
```

## Runs that may have missed items

`unchanged + updated` is how many fetched items an earlier run had already stored. When it is 0 on a run that is not the first one, the feed moved past items between this run and the previous one.

```sql
SELECT s.slug, r.started_at, r.fetched, r.inserted
FROM collect_run r
JOIN source s ON s.id = r.source_id
WHERE r.status = 'ok'
  AND r.unchanged + r.updated = 0
  AND r.started_at > (SELECT min(started_at) FROM collect_run WHERE source_id = r.source_id)
ORDER BY r.started_at;
```

## Overlap margin

A run that shares few items with the previous one came close to missing some. The margin is thinnest at the weekday peak (10:00–18:59 Kyiv), so it is counted apart from the rest of the week.

```sql
SELECT s.slug,
       extract(isodow FROM r.started_at AT TIME ZONE 'Europe/Kyiv') <= 5
           AND extract(hour FROM r.started_at AT TIME ZONE 'Europe/Kyiv') BETWEEN 10 AND 18
           AS weekday_peak,
       count(*)                                                                AS runs,
       min(r.unchanged + r.updated)                                            AS min_overlap,
       percentile_disc(0.1) WITHIN GROUP (ORDER BY r.unchanged + r.updated)    AS p10,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY r.unchanged + r.updated)    AS median
FROM collect_run r
JOIN source s ON s.id = r.source_id
WHERE r.status = 'ok'
  AND r.started_at > now() - interval '7 days'
GROUP BY 1, 2
ORDER BY 1, 2;
```

## Feed window

How many hours of postings one fetch covers, for each hour of the day: the feed's size divided by the new items per hour. New items per hour are the items each run inserted, divided by the time since the source's previous run. The shortest window is at the weekday peak; half of it is the longest safe gap between runs.

```sql
WITH runs AS (
    SELECT s.slug, r.started_at, r.fetched, r.inserted,
           r.started_at - lag(r.started_at) OVER (PARTITION BY r.source_id ORDER BY r.started_at)
               AS gap
    FROM collect_run r
    JOIN source s ON s.id = r.source_id
    WHERE r.status = 'ok'
), hourly AS (
    SELECT slug,
           extract(isodow FROM started_at AT TIME ZONE 'Europe/Kyiv') <= 5 AS weekday,
           extract(hour FROM started_at AT TIME ZONE 'Europe/Kyiv')        AS hour,
           max(fetched)                                                     AS feed,
           sum(inserted) / (extract(epoch FROM sum(gap)) / 3600)            AS per_hour
    FROM runs
    WHERE started_at > now() - interval '7 days'
    GROUP BY 1, 2, 3
)
SELECT slug, weekday, hour,
       round(per_hour, 1)                     AS new_per_hour,
       round(feed / nullif(per_hour, 0), 1)   AS window_hours
FROM hourly
ORDER BY slug, weekday, hour;
```

## Recent failures

```sql
SELECT s.slug, r.started_at, r.error_type, r.error
FROM collect_run r
JOIN source s ON s.id = r.source_id
WHERE r.status = 'failed'
ORDER BY r.started_at DESC
LIMIT 20;
```

## Interrupted runs

A run is `running` only while its request is in flight, which takes seconds. An older one was interrupted. A few after deploys or crashes are expected. More of them mean the collector keeps dying after its request, or cannot write to the database.

```sql
SELECT s.slug, r.started_at
FROM collect_run r
JOIN source s ON s.id = r.source_id
WHERE r.status = 'running'
  AND r.started_at < now() - interval '1 hour'
ORDER BY r.started_at DESC;
```

## Database size

The Neon Free plan allows 1 GB per project.

```sql
SELECT pg_size_pretty(pg_database_size(current_database()));
```

## Measured 2026-11-06 to 2026-11-12

The queries above, run on Neon on 2026-11-13 over the runs from the first cron-job.org dispatch (2026-11-06 12:33 UTC) to 2026-11-13 00:00 Kyiv. Full days are 2026-11-07 to 11-12, a weekend included.

| | Djinni | DOU |
|---|---|---|
| `ok` runs per full day | 48 every day | 35–38 |
| Failed / interrupted runs | 0 / 0 | 0 / 0 |
| Gap p50 / p99 / max | 30 / 32 / 50 min | 45 / 45 / 65 min |
| Runs that may have missed items | 0 | 0 |
| Overlap at the weekday peak: min / p10 / median | 45 / 67 / 79 of 100 | 16 / 23 / 34 of 50 |
| Overlap at other times: min / median | 75 / 100 | 25 / 50 |
| Feed window at the busiest weekday hour | 1.7 h (12:00, ~60 new items/h) | 1.5 h (17:00, ~33 new items/h) |
| Feed window at other weekday peak hours | 2.0–3.3 h | 1.6–2.4 h |
| New items on Saturday / Sunday | 30 / 23 | 12 / 8 |

The database held 3814 raw items (Djinni 2424, DOU 1390) in 20 MB.

- **No items were missed** after the cron-job.org trigger. The only two runs that missed items are DOU's at 09:53 and 12:33 UTC on 2026-11-06, before it (see [Triggers](#triggers)).
- **Djinni has a wide margin.** Its 30-minute gaps are under a third of its shortest window, and every run at the peak shared at least 45 of its 100 items with the previous one.
- **DOU is fetched every 45 minutes more often than every 30**: 143 gaps of 45 minutes and 82 of 30 (see [Runs per day](#runs-per-day)). 45 minutes is half its shortest window, so the margin is thin. No item was missed, and the smallest overlap was 16 of 50, but the busiest single run brought 31 new items in 30 minutes (2026-11-10, 10:52 Kyiv): at that pace a 45-minute gap leaves about 4.
- **The weekend is quiet.** Both feeds barely move on Saturday and Sunday, and at night a fetch covers a day or more.
- **The windows are shorter than the first samples** in `docs/sources.md` (2.5 h for Djinni, 1.7 h for DOU), which were taken outside the busiest hour.
- One delayed dispatch made the longest gaps: Djinni 50 and DOU 65 minutes on 2026-11-11 at 22:57 Kyiv, a quiet hour.
