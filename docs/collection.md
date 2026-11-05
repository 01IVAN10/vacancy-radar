# Checking collection

Every request to a source is a row in `collect_run`: `ok`, `failed`, or `dry_run` (fetched without storing items). These questions are answered with SQL instead of job logs.

- Did collection run on schedule?
- Did it fail?
- Did the feed move past items between two runs?

Run the queries with `psql` against the hosted database, or through Docker without a local client:

```sh
docker run --rm -i -e PGURL="$(sed -n 's/^DATABASE_URL=//p' .env.neon | sed 's/+psycopg//')" \
  postgres:16 sh -c 'psql "$PGURL"' < query.sql
```

## Runs per day

The schedule runs every 15 minutes, and each source is fetched only when its `min_interval` has passed. Expect about 48 `ok` runs per day for Djinni (every ~30 minutes) and 32–48 for DOU (every 30–45 minutes). Runs skipped as not due leave no row.

```sql
SELECT s.slug,
       date_trunc('day', r.started_at AT TIME ZONE 'Europe/Kyiv') AS day,
       count(*) FILTER (WHERE r.status = 'ok')     AS ok,
       count(*) FILTER (WHERE r.status = 'failed') AS failed,
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

## Database size

The Neon Free plan allows 1 GB per project.

```sql
SELECT pg_size_pretty(pg_database_size(current_database()));
```
