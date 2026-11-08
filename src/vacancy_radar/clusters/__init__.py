"""Clusters: which vacancies are the same job, however many sources posted it.

A cluster is the unit everything downstream counts and sends: notifications
go out once per cluster, and analytics counts clusters, not postings.
`service.assign_clusters` gives each new vacancy a cluster; for now every
vacancy is a cluster of its own. Deduplication (stage 8) replaces that rule,
with the pure fingerprint and similarity scoring in `domain/`.
"""
