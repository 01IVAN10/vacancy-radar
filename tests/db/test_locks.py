from vacancy_radar.db import locks


def test_every_job_has_a_namespace_of_its_own() -> None:
    namespaces = {name: value for name, value in vars(locks).items() if name.isupper()}

    assert namespaces
    assert len(set(namespaces.values())) == len(namespaces)
