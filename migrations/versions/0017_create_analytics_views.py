"""materialized views for market analytics

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-15 12:00:00.000000

Analytics counts jobs, not postings: a job posted on Djinni and DOU is one
cluster (docs/deduplication.md). Two views describe each job, and the
aggregates are built from them:

- `analytics_job`: one row per job, from all its postings together. Its
  skills and levels are those any posting names, and its salary is the
  monthly dollar salary of the first posting that has one. No posting
  "shows" the job here, so analytics never has to agree with the order that
  picks the one the search page and Telegram show.
- `analytics_job_source`: when the job first appeared on each source. A
  posting can join a cluster weeks after the job first appeared elsewhere,
  and a source's weekly series counts it in the week it appeared there, so
  past weeks do not change.

Days are Kyiv days. `analytics.refresh` rebuilds the views in the order they
are created here, with `REFRESH ... CONCURRENTLY`, which needs the unique
index each one has and never blocks readers. docs/analytics.md has the
definitions.

Skills and salaries count only jobs that name at least one skill from the
dictionary: Djinni's feed has every job, sales and HR included, and their
salaries would say nothing about the IT market.

The word 'all' marks the series of every level and of every source; it is
`domain.analytics.ALL`. The views read `vacancy` columns, so a later
migration that changes the type of one of them must drop and recreate them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB = """
CREATE MATERIALIZED VIEW analytics_job AS
WITH postings AS (
    SELECT cluster_id, count(*)::int AS postings
    FROM vacancy
    WHERE cluster_id IS NOT NULL
    GROUP BY cluster_id
), skills AS (
    SELECT v.cluster_id, array_agg(DISTINCT skill ORDER BY skill) AS skills
    FROM vacancy v CROSS JOIN LATERAL unnest(v.skills) AS skill
    WHERE v.cluster_id IS NOT NULL
    GROUP BY v.cluster_id
), levels AS (
    SELECT v.cluster_id, array_agg(DISTINCT level ORDER BY level) AS seniority
    FROM vacancy v CROSS JOIN LATERAL unnest(v.seniority) AS level
    WHERE v.cluster_id IS NOT NULL
    GROUP BY v.cluster_id
), pay AS (
    SELECT DISTINCT ON (cluster_id)
        cluster_id,
        CASE
            WHEN salary_usd_min IS NOT NULL AND salary_usd_max IS NOT NULL
                THEN (salary_usd_min + salary_usd_max) / 2.0
            ELSE coalesce(salary_usd_min, salary_usd_max)
        END AS salary_usd
    FROM vacancy
    WHERE cluster_id IS NOT NULL
        AND (salary_usd_min IS NOT NULL OR salary_usd_max IS NOT NULL)
    ORDER BY cluster_id, id
)
SELECT
    c.id AS job_id,
    (c.first_seen_at AT TIME ZONE 'Europe/Kyiv')::date AS first_seen_on,
    p.postings,
    coalesce(s.skills, '{}') AS skills,
    coalesce(l.seniority, '{}') AS seniority,
    pay.salary_usd
FROM postings p
JOIN vacancy_cluster c ON c.id = p.cluster_id
LEFT JOIN skills s ON s.cluster_id = c.id
LEFT JOIN levels l ON l.cluster_id = c.id
LEFT JOIN pay ON pay.cluster_id = c.id
"""

JOB_SOURCE = """
CREATE MATERIALIZED VIEW analytics_job_source AS
SELECT
    v.cluster_id AS job_id,
    s.slug AS source,
    (min(r.first_seen_at) AT TIME ZONE 'Europe/Kyiv')::date AS first_seen_on
FROM vacancy v
JOIN raw_vacancy r ON r.id = v.raw_vacancy_id
JOIN source s ON s.id = v.source_id
WHERE v.cluster_id IS NOT NULL
GROUP BY v.cluster_id, s.slug
"""

SKILL_MONTHLY = """
CREATE MATERIALIZED VIEW analytics_skill_monthly AS
WITH with_skills AS (
    SELECT date_trunc('month', first_seen_on)::date AS month, skills
    FROM analytics_job
    WHERE cardinality(skills) > 0
)
SELECT w.month, skill, count(*)::int AS n, totals.jobs
FROM with_skills w
CROSS JOIN LATERAL unnest(w.skills) AS skill
JOIN (SELECT month, count(*)::int AS jobs FROM with_skills GROUP BY month) totals USING (month)
GROUP BY w.month, skill, totals.jobs
"""

SALARY_WEEKLY = """
CREATE MATERIALIZED VIEW analytics_salary_weekly AS
WITH paid AS (
    SELECT date_trunc('week', first_seen_on)::date AS week, seniority, salary_usd
    FROM analytics_job
    WHERE salary_usd IS NOT NULL AND cardinality(skills) > 0
), by_level AS (
    SELECT week, 'all' AS level, salary_usd FROM paid
    UNION ALL
    SELECT week, level, salary_usd FROM paid CROSS JOIN LATERAL unnest(seniority) AS level
)
SELECT
    week,
    level,
    count(*)::int AS n,
    round(percentile_cont(0.25) WITHIN GROUP (ORDER BY salary_usd))::int AS p25,
    round(percentile_cont(0.5) WITHIN GROUP (ORDER BY salary_usd))::int AS median,
    round(percentile_cont(0.75) WITHIN GROUP (ORDER BY salary_usd))::int AS p75
FROM by_level
GROUP BY week, level
"""

JOBS_WEEKLY = """
CREATE MATERIALIZED VIEW analytics_jobs_weekly AS
SELECT
    date_trunc('week', first_seen_on)::date AS week,
    'all' AS source,
    count(*)::int AS jobs,
    count(*) FILTER (WHERE cardinality(skills) > 0)::int AS with_skills
FROM analytics_job
GROUP BY 1
UNION ALL
SELECT
    date_trunc('week', js.first_seen_on)::date,
    js.source,
    count(*)::int,
    count(*) FILTER (WHERE cardinality(j.skills) > 0)::int
FROM analytics_job_source js
JOIN analytics_job j USING (job_id)
GROUP BY 1, 2
"""

SUMMARY = """
CREATE MATERIALIZED VIEW analytics_summary AS
SELECT
    1 AS id,
    now() AS refreshed_at,
    count(*)::int AS jobs,
    coalesce(sum(postings), 0)::int AS postings,
    count(*) FILTER (WHERE cardinality(skills) > 0)::int AS with_skills,
    count(*) FILTER (WHERE cardinality(skills) > 0 AND salary_usd IS NOT NULL)::int AS with_salary,
    min(first_seen_on) AS first_day,
    max(first_seen_on) AS last_day
FROM analytics_job
"""

# In dependency order, each with the columns of the unique index that
# REFRESH ... CONCURRENTLY needs.
VIEWS = {
    "analytics_job": (JOB, "job_id"),
    "analytics_job_source": (JOB_SOURCE, "job_id, source"),
    "analytics_skill_monthly": (SKILL_MONTHLY, "month, skill"),
    "analytics_salary_weekly": (SALARY_WEEKLY, "week, level"),
    "analytics_jobs_weekly": (JOBS_WEEKLY, "week, source"),
    "analytics_summary": (SUMMARY, "id"),
}


def upgrade() -> None:
    for name, (definition, key) in VIEWS.items():
        op.execute(sa.text(definition))
        op.execute(sa.text(f"CREATE UNIQUE INDEX uq_{name} ON {name} ({key})"))


def downgrade() -> None:
    for name in reversed(VIEWS):
        op.execute(sa.text(f"DROP MATERIALIZED VIEW {name}"))
