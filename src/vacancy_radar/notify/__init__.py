"""Matched vacancies sent to Telegram: matching, messages and delivery.

Notify reads clusters from `vacancy_radar.clusters`. `matching` queues the
clusters that match each subscription as `notification` rows, `message`
renders them, and `sending` delivers them. `tasks` holds the thin Celery
wrappers.
"""
