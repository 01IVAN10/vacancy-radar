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

## Runs per day

The triggers start the job about every 7.5 minutes, and each source is fetched only when its `min_interval` has passed. Expect about 48 `ok` runs per day for Djinni (every ~30 minutes) and 38–48 for DOU (every 30–38 minutes). Runs skipped as not due leave no row.

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

GitHub delays scheduled jobs under load. A gap longer than half the feed's window (about 75 minutes for Djinni, see `docs/sources.md`) risks missed items.

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
