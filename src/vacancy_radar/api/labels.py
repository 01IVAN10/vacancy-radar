"""How the fixed vocabularies are named on pages, shared by every form that offers them."""

from typing import Final

from vacancy_radar.domain.search import PERIOD_DAYS, Sort
from vacancy_radar.domain.seniority import LEVELS
from vacancy_radar.domain.skills import SKILLS, Category
from vacancy_radar.domain.work_format import WORK_FORMATS

CATEGORY_LABELS: Final[dict[Category, str]] = {
    "language": "Languages",
    "backend": "Backend",
    "frontend": "Frontend",
    "mobile": "Mobile",
    "database": "Databases",
    "data": "Data",
    "ml": "Machine learning",
    "cloud": "Cloud",
    "devops": "DevOps",
    "qa": "QA",
    "embedded": "Embedded",
    "tool": "Tools",
}
LEVEL_LABELS: Final = {level: level.capitalize() for level in LEVELS}
FORMAT_LABELS: Final = {work_format: work_format.capitalize() for work_format in WORK_FORMATS}
# Skills by category, in dictionary order, for the form's checkbox groups.
SKILL_GROUPS: Final = [
    (label, [skill for skill in SKILLS if skill.category == category])
    for category, label in CATEGORY_LABELS.items()
]
PERIOD_LABELS: Final = {
    str(days): "Last 24 hours" if days == 1 else f"Last {days} days" for days in PERIOD_DAYS
}
SORT_LABELS: Final[dict[Sort, str]] = {"relevance": "Most relevant", "newest": "Newest"}
