"""Which vacancy shows a cluster, and what it shows.

A job posted on several sites is one cluster, and Telegram messages and
site pages show it by one of its vacancies. Both prefer the most complete
one (`fullest_first`), so they agree on what a job looks like.
"""

from typing import Any

from sqlalchemy import ColumnElement

from vacancy_radar.db.models import Vacancy
from vacancy_radar.domain.card import VacancyCard


def fullest_first() -> tuple[ColumnElement[Any], ...]:
    """`ORDER BY` terms that put the most complete vacancy of a cluster first.

    One with a salary first, then one with a company, then the lowest id
    (PLAN.md, stage 8 decisions). False sorts before true.
    """
    return (Vacancy.salary_currency.is_(None), Vacancy.company.is_(None), Vacancy.id.asc())


def card_of(vacancy: Vacancy, source_name: str) -> VacancyCard:
    """What is shown about a stored vacancy of the source named `source_name`."""
    return VacancyCard(
        title=vacancy.title,
        url=vacancy.url,
        source_name=source_name,
        company=vacancy.company,
        cities=tuple(vacancy.cities),
        salary_min=vacancy.salary_min,
        salary_max=vacancy.salary_max,
        salary_currency=vacancy.salary_currency,
        salary_period=vacancy.salary_period,
        seniority=tuple(vacancy.seniority),
        work_formats=tuple(vacancy.work_formats),
        skills=tuple(vacancy.skills),
    )
