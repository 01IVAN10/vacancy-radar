"""Reprocessing: rebuild `vacancy` rows from `raw_vacancy` with the domain parsers.

Run it with `python -m vacancy_radar.normalize`. Celery Beat runs the same
code as the `normalize.pending` task.
"""
