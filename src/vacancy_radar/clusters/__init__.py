"""Clusters: which vacancies are the same job, however many sources posted it.

A cluster is the unit everything downstream counts and sends: notifications
go out once per cluster, and analytics counts clusters, not postings.
`service.assign_clusters` gives each new vacancy a cluster with the rule in
`domain/dedup.py`. `notify.match` runs it before matching, and
`python -m vacancy_radar.clusters` runs it by hand.
"""
