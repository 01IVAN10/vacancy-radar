"""materialized views for market analytics

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-15 12:00:00.000000

Analytics counts jobs, not postings: a job posted on Djinni and DOU is one
cluster (docs/deduplication.md). `analytics_job` is a plain view with one row
per job, read from the vacancy that shows it: one with a dollar salary first,
then the fullest (salary, company, lowest id), as on the search page. The
materialized views aggregate it by Kyiv week or month of the job's first
sighting, and `analytics.refresh` rebuilds them with `REFRESH ...
CONCURRENTLY`, which needs the unique index each one has and never blocks
readers. docs/analytics.md has the definitions.

Skills and salaries count only jobs that name at least one skill from the
dictionary: Djinni's feed has every job, sales and HR included, and their
salaries would say nothing about the IT market.

The views read `vacancy` columns, so a later migration that changes the type
of one of them must drop and recreate the views.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB = """
CREATE VIEW analytics_job AS
SELECT DISTINCT ON (v.cluster_id)
    v.cluster_id AS job_id,
    (c.first_seen_at AT TIME ZONE 'Europe/Kyiv')::date AS first_seen_on,
    v.skills,
    v.seniority,
    CASE
        WHEN v.salary_usd_min IS NOT NULL AND v.salary_usd_max IS NOT NULL
            THEN (v.salary_usd_min + v.salary_usd_max) / 2.0
        ELSE coalesce(v.salary_usd_min, v.salary_usd_max)
    END AS salary_usd
FROM vacancy v
JOIN vacancy_cluster c ON c.id = v.cluster_id
ORDER BY v.cluster_id,
    (v.salary_usd_min IS NULL AND v.salary_usd_max IS NULL),
    v.salary_currency IS NULL,
    v.company IS NULL,
    v.id
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
WITH sources AS (
    SELECT DISTINCT v.cluster_id AS job_id, s.slug
    FROM vacancy v
    JOIN source s ON s.id = v.source_id
    WHERE v.cluster_id IS NOT NULL
), jobs AS (
    SELECT job_id, date_trunc('week', first_seen_on)::date AS week,
        cardinality(skills) > 0 AS with_skills
    FROM analytics_job
), by_source AS (
    SELECT week, 'all' AS source, with_skills FROM jobs
    UNION ALL
    SELECT jobs.week, sources.slug, jobs.with_skills FROM jobs JOIN sources USING (job_id)
)
SELECT
    week,
    source,
    count(*)::int AS jobs,
    count(*) FILTER (WHERE with_skills)::int AS with_skills
FROM by_source
GROUP BY week, source
"""

SUMMARY = """
CREATE MATERIALIZED VIEW analytics_summary AS
SELECT
    1 AS id,
    now() AS refreshed_at,
    count(*)::int AS jobs,
    (SELECT count(*)::int FROM vacancy WHERE cluster_id IS NOT NULL) AS postings,
    count(*) FILTER (WHERE cardinality(skills) > 0)::int AS with_skills,
    count(*) FILTER (WHERE cardinality(skills) > 0 AND salary_usd IS NOT NULL)::int AS with_salary,
    min(first_seen_on) AS first_day,
    max(first_seen_on) AS last_day
FROM analytics_job
"""

# Each view with the columns of its unique index, which REFRESH ... CONCURRENTLY needs.
VIEWS = {
    "analytics_skill_monthly": (SKILL_MONTHLY, "month, skill"),
    "analytics_salary_weekly": (SALARY_WEEKLY, "week, level"),
    "analytics_jobs_weekly": (JOBS_WEEKLY, "week, source"),
    "analytics_summary": (SUMMARY, "id"),
}


def upgrade() -> None:
    op.execute(sa.text(JOB))
    for name, (definition, key) in VIEWS.items():
        op.execute(sa.text(definition))
        op.execute(sa.text(f"CREATE UNIQUE INDEX uq_{name} ON {name} ({key})"))


def downgrade() -> None:
    for name in reversed(VIEWS):
        op.execute(sa.text(f"DROP MATERIALIZED VIEW {name}"))
    op.execute(sa.text("DROP VIEW analytics_job"))
