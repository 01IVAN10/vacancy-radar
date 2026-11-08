"""Matched vacancies sent to Telegram: clusters, matching, messages and delivery.

`clusters` puts vacancies into clusters, `matching` queues the clusters that
match each subscription as `notification` rows, `message` renders them, and
`sending` delivers them. `tasks` holds the thin Celery wrappers.
"""
