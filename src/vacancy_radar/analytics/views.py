"""The analytics views of migration 0017, as SQLAlchemy Core tables to query.

They have a `MetaData` of their own, not the models' `Base.metadata`: they
are views, not tables, so Alembic's comparison of models and database must
not see them. docs/analytics.md describes each one.
"""

from typing import Final

from sqlalchemy import Column, Date, DateTime, Integer, MetaData, Table, Text

_views = MetaData()

# Jobs that name a skill, by Kyiv month of first sighting: `n` name `skill`, `jobs` in all.
skill_monthly: Final = Table(
    "analytics_skill_monthly",
    _views,
    Column("month", Date),
    Column("skill", Text),
    Column("n", Integer),
    Column("jobs", Integer),
)
# Monthly salaries in US dollars of jobs that name a skill, by week and level ("all" or one).
salary_weekly: Final = Table(
    "analytics_salary_weekly",
    _views,
    Column("week", Date),
    Column("level", Text),
    Column("n", Integer),
    Column("p25", Integer),
    Column("median", Integer),
    Column("p75", Integer),
)
# Jobs first seen in a week, on one source or on any ("all").
jobs_weekly: Final = Table(
    "analytics_jobs_weekly",
    _views,
    Column("week", Date),
    Column("source", Text),
    Column("jobs", Integer),
    Column("with_skills", Integer),
)
# One row: totals, the days the data cover, and when the views were refreshed.
summary: Final = Table(
    "analytics_summary",
    _views,
    Column("id", Integer),
    Column("refreshed_at", DateTime(timezone=True)),
    Column("jobs", Integer),
    Column("postings", Integer),
    Column("with_skills", Integer),
    Column("with_salary", Integer),
    Column("first_day", Date),
    Column("last_day", Date),
)

# In the order they are refreshed: the summary last, so its time covers the others.
MATERIALIZED_VIEWS: Final = (skill_monthly, salary_weekly, jobs_weekly, summary)
