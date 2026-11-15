"""The source registry and the normalizer registry name the same sources."""

from vacancy_radar.domain.normalize import NORMALIZERS
from vacancy_radar.sources import ADAPTERS


def test_every_adapter_has_a_normalizer() -> None:
    """A source with an adapter but no normalizer is collected and then skipped.

    `pending_batch` selects raw items only for slugs in `NORMALIZERS`, so a
    missing normalizer is silent. The registries stay separate because
    normalization is pure and lives in `domain/`; this test keeps them in step.
    """
    assert set(ADAPTERS) == set(NORMALIZERS)
