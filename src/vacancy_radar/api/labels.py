"""How the fixed vocabularies are named on pages, shared by every form that offers them."""

from typing import Final

from vacancy_radar.domain.seniority import LEVELS
from vacancy_radar.domain.skills import SKILLS, Category
from vacancy_radar.domain.work_format import WORK_FORMATS
from vacancy_radar.sources import ADAPTERS

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
# Sources by slug, as their adapters name them.
SOURCE_LABELS: Final = {slug: adapter.name for slug, adapter in ADAPTERS.items()}
