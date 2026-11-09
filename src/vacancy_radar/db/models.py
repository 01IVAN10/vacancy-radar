"""Tables for collected data and for the people who subscribe to it.

`raw_vacancy` keeps every fetched item exactly as the source returned it, so
the normalized tables of later stages can be rebuilt from it at any time.
`collect_run` records every collection attempt, so gaps and missed items can
be found with SQL instead of reading job logs. `exchange_rate` caches the
official NBU rates that salaries are converted with.

`vacancy` is derived: one row per `raw_vacancy`, rebuilt by the reprocessing
task whenever the raw item or the parsers change. `skill` mirrors the skill
dictionary in `domain/skills.py`, so SQL can show skill names.

`vacancy_cluster` groups the vacancies that are one job posted on several
sources or reposted (`clusters/service.py`, `docs/deduplication.md`).

`app_user` holds accounts, and `subscription` the filters each account
wants vacancies sent for. `telegram_link_token` holds the one-time tokens
that link an account to a Telegram chat. `notification` records the clusters
each subscription matched, and `delivery` the one message per account and
cluster that tells about them. `app_user` is not called `user` because that is a
reserved word in Postgres: an unquoted `SELECT * FROM user` returns the
name of the current database role instead of failing.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from vacancy_radar.db.base import Base
from vacancy_radar.domain.delivery import DELIVERY_STATUSES, DeliveryStatus
from vacancy_radar.domain.seniority import LEVELS
from vacancy_radar.domain.subscriptions import MAX_NAME_LENGTH, SKILLS_MATCHES
from vacancy_radar.domain.work_format import WORK_FORMATS


def _known_values(column: str, values: tuple[str, ...]) -> str:
    """A CHECK that an array column holds only the given values."""
    listed = ", ".join(f"'{value}'" for value in values)
    return f"{column} <@ ARRAY[{listed}]::text[]"


def _one_of(column: str, values: tuple[str, ...]) -> str:
    """A CHECK that a text column holds one of the given values."""
    listed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({listed})"


class Source(Base):
    """A site vacancies are collected from, such as Djinni."""

    __tablename__ = "source"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RawVacancy(Base):
    """One item from a source feed, stored unchanged.

    `(source_id, external_id)` is unique, so collecting the same feed twice
    updates rows instead of duplicating them. `updated_at` moves only when the
    payload actually changes.
    """

    __tablename__ = "raw_vacancy"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="RESTRICT"))
    external_id: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CollectRun(Base):
    """One attempt to collect one source, successful or not.

    The row is committed as `running` before the request is sent, so every
    request is counted even if the collector fails or dies after sending it.
    It then becomes `ok`, `failed` or `dry_run`. A run left `running` was
    interrupted and has no `finished_at`.

    A run with `status = 'ok'` carries the counts from `CollectStats`. A failed
    run carries the error instead. A `dry_run` fetched the source without
    storing items, so it has only `fetched` and `skipped`. Every status counts
    as a request to the source for its `min_interval`. `unchanged + updated` is
    the overlap with earlier runs: an `ok` run where it is 0 means the feed
    moved past items between two runs.
    """

    __tablename__ = "collect_run"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'ok', 'failed', 'dry_run')", name="status"),
        CheckConstraint("(status = 'running') = (finished_at IS NULL)", name="finished"),
        Index("ix_collect_run_source_id_started_at", "source_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="RESTRICT"))
    # Both come from the collector's clock, so `finished_at - started_at` is the
    # run's duration. A server-side `now()` would be the transaction start time
    # on another machine's clock.
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(16))
    fetched: Mapped[int | None]
    skipped: Mapped[int | None]
    inserted: Mapped[int | None]
    updated: Mapped[int | None]
    unchanged: Mapped[int | None]
    error_type: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class ExchangeRate(Base):
    """The official NBU rate of one currency on one day, in hryvnias per unit.

    NBU sets a rate for every calendar day, weekends included, so each day a
    vacancy was published on has its own row. Rates never change once set,
    which makes this table a cache: a day is requested from NBU only once.
    """

    __tablename__ = "exchange_rate"
    __table_args__ = (
        CheckConstraint("currency IN ('USD', 'EUR')", name="currency"),
        CheckConstraint("uah_per_unit > 0", name="uah_per_unit_positive"),
    )

    day: Mapped[date] = mapped_column(primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    uah_per_unit: Mapped[Decimal] = mapped_column(Numeric(12, 6))


class Vacancy(Base):
    """A vacancy normalized from one `raw_vacancy` row.

    The row is fully derived: deleting the raw item deletes it, and the
    reprocessing task rewrites it when `raw_updated_at` no longer matches
    the raw item or `normalizer_version` is older than the current parsers.

    The salary is stored as the vacancy states it (`salary_*`) and as whole
    US dollars per month (`salary_usd_*`), which is NULL for hourly rates.
    """

    __tablename__ = "vacancy"
    __table_args__ = (
        CheckConstraint("salary_currency IN ('USD', 'EUR', 'UAH')", name="salary_currency"),
        CheckConstraint("salary_period IN ('month', 'year', 'hour')", name="salary_period"),
        CheckConstraint("salary_min <= salary_max", name="salary_range"),
        # The dollar range is converted from the stated one, so it keeps its order.
        CheckConstraint("salary_usd_min <= salary_usd_max", name="salary_usd_range"),
        # A salary has a currency, a period and at least one bound, or none of them.
        CheckConstraint(
            "(salary_currency IS NULL) = (salary_period IS NULL) "
            "AND (salary_currency IS NULL) = (salary_min IS NULL AND salary_max IS NULL)",
            name="salary_complete",
        ),
        CheckConstraint(_known_values("seniority", LEVELS), name="seniority_known"),
        CheckConstraint(_known_values("work_formats", WORK_FORMATS), name="work_formats_known"),
        # Trigrams of the title: deduplication finds its candidates with `title % :title`.
        Index(
            "ix_vacancy_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    raw_vacancy_id: Mapped[int] = mapped_column(
        ForeignKey("raw_vacancy.id", ondelete="CASCADE"), unique=True
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="RESTRICT"))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    cities: Mapped[list[str]] = mapped_column(ARRAY(Text))
    category: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None]
    salary_min: Mapped[int | None]
    salary_max: Mapped[int | None]
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    salary_period: Mapped[str | None] = mapped_column(String(8))
    salary_usd_min: Mapped[int | None]
    salary_usd_max: Mapped[int | None]
    seniority: Mapped[list[str]] = mapped_column(ARRAY(Text))
    work_formats: Mapped[list[str]] = mapped_column(ARRAY(Text))
    # Slugs from `domain/skills.py`. The GIN index comes in stage 9, measured.
    skills: Mapped[list[str]] = mapped_column(ARRAY(Text))
    # Plain text of the source's description, for deduplication and search.
    # Pages show only an excerpt of it and link to the original.
    description: Mapped[str] = mapped_column(Text)
    normalizer_version: Mapped[int] = mapped_column(SmallInteger)
    raw_updated_at: Mapped[datetime]
    normalized_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Set once by `clusters.service.assign_clusters`, never by normalization, so
    # a rebuilt vacancy keeps its cluster and is not sent again.
    cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("vacancy_cluster.id", ondelete="RESTRICT"), index=True
    )


class VacancyCluster(Base):
    """One job, however many sources posted it.

    Clusters only grow: a new vacancy joins one or opens its own, and two
    clusters are never merged, so the deliveries keyed by a cluster stay valid.

    `first_seen_at` is the earliest `raw_vacancy.first_seen_at` of its
    vacancies, not the time the cluster was created. Rebuilding the derived
    tables from raw data therefore never makes an old vacancy look new, and
    notifications compare it with when a subscription started: a job that
    appeared before a subscription is never sent to it, whichever of its
    postings arrives later.
    """

    __tablename__ = "vacancy_cluster"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Skill(Base):
    """One entry of the skill dictionary, synced from `domain/skills.py`."""

    __tablename__ = "skill"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(16))


class User(Base):
    """An account that signs in with an email and a password.

    Emails are stored in lower case, and a CHECK keeps it that way, so the
    unique constraint is case-insensitive without `citext` or an expression
    index.

    `telegram_chat_id` is the private chat matches are sent to, set by
    `/start` with a link token. It is unique: one chat belongs to one
    account. It is set together with `telegram_linked_at`, or neither is.
    """

    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint(
            "(telegram_chat_id IS NULL) = (telegram_linked_at IS NULL)", name="telegram_linked"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    # Argon2 in PHC string format, parameters included, as pwdlib writes it.
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    telegram_linked_at: Mapped[datetime | None]


class TelegramLinkToken(Base):
    """A one-time token that links the account to the Telegram chat that sends it.

    Only its SHA-256 hash is stored, so the table does not hold a usable
    token. An account has at most one: asking for a new link replaces it.
    Using it deletes it.
    """

    __tablename__ = "telegram_link_token"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), unique=True)
    expires_at: Mapped[datetime]


class Subscription(Base):
    """One account's filter over vacancies; see `domain/subscriptions.py`.

    The CHECKs keep each filter in the canonical form the domain builds:
    `skills_match` is 'any' unless two or more skills are chosen, and
    `include_unsalaried` is set exactly when `min_salary_usd` is.

    `skills` holds slugs from `domain/skills.py` but has no CHECK: the
    dictionary changes, and a removed skill must not make stored rows
    invalid. The form refuses unknown slugs instead. Levels and work formats
    are fixed vocabularies, so the database checks them.
    """

    __tablename__ = "subscription"
    __table_args__ = (
        CheckConstraint(f"char_length(name) BETWEEN 1 AND {MAX_NAME_LENGTH}", name="name_length"),
        CheckConstraint(_one_of("skills_match", SKILLS_MATCHES), name="skills_match"),
        CheckConstraint(
            "skills_match = 'any' OR cardinality(skills) >= 2", name="skills_match_needs_skills"
        ),
        CheckConstraint(_known_values("seniority", LEVELS), name="seniority_known"),
        CheckConstraint(_known_values("work_formats", WORK_FORMATS), name="work_formats_known"),
        CheckConstraint("min_salary_usd > 0", name="min_salary_usd_positive"),
        CheckConstraint(
            "(min_salary_usd IS NULL) = (include_unsalaried IS NULL)", name="salary_complete"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    skills: Mapped[list[str]] = mapped_column(ARRAY(Text))
    skills_match: Mapped[str] = mapped_column(String(3))
    seniority: Mapped[list[str]] = mapped_column(ARRAY(Text))
    work_formats: Mapped[list[str]] = mapped_column(ARRAY(Text))
    min_salary_usd: Mapped[int | None]
    include_unsalaried: Mapped[bool | None]
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    # When the subscription was created or last resumed. Only clusters first
    # seen since then are sent, so resuming does not send what came during a pause.
    active_since: Mapped[datetime] = mapped_column(server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Delivery(Base):
    """One Telegram message about one cluster to one account; see `domain/delivery.py`.

    `(user_id, cluster_id)` is unique, so the database, not timing, keeps a
    cluster from reaching the same chat twice: a subscription that matches
    later joins the existing delivery instead of making a new one. It keeps
    no `vacancy_id`: the vacancy to show is chosen from the cluster when the
    message is sent, so rebuilding vacancies or adding one to the cluster
    never erases the record of what was sent. `cluster_id` is RESTRICT:
    clusters are never merged or deleted while a delivery points at them.
    """

    __tablename__ = "delivery"
    __table_args__ = (
        UniqueConstraint("user_id", "cluster_id"),
        CheckConstraint(_one_of("status", DELIVERY_STATUSES), name="status"),
        CheckConstraint("(status = 'sent') = (sent_at IS NOT NULL)", name="sent"),
        Index(
            "ix_delivery_pending_next_attempt_at",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("vacancy_cluster.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[DeliveryStatus] = mapped_column(String(8))
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    next_attempt_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # When the last attempt was claimed; with `sending`, how long it has hung.
    claimed_at: Mapped[datetime | None]
    sent_at: Mapped[datetime | None]
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)


class Notification(Base):
    """A cluster that matched a subscription, and the delivery that tells its account.

    `(subscription_id, cluster_id)` is unique, so matching the same cluster
    again records nothing new. Its delivery's message names every
    subscription matched by the time it is sent.
    """

    __tablename__ = "notification"
    __table_args__ = (UniqueConstraint("subscription_id", "cluster_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscription.id", ondelete="CASCADE"))
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("vacancy_cluster.id", ondelete="RESTRICT"), index=True
    )
    delivery_id: Mapped[int] = mapped_column(
        ForeignKey("delivery.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
