"""Declarative base shared by all models."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase

# Explicit constraint names keep Alembic migrations stable and reviewable:
# without them Postgres picks names that autogenerate cannot reproduce.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Every timestamp is `timestamptz`: sources publish in Kyiv time, workers run in UTC.
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: DateTime(timezone=True)}
