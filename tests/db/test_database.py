import psycopg
import pytest
from psycopg import sql


def test_runs_on_postgres_16(db_conn: psycopg.Connection) -> None:
    row = db_conn.execute("SHOW server_version_num").fetchone()

    assert row is not None
    assert int(row[0]) // 10000 == 16


@pytest.mark.parametrize("extension", ["pg_trgm", "unaccent"])
def test_required_extension_is_available(db_conn: psycopg.Connection, extension: str) -> None:
    # Created inside the test transaction, then rolled back by the fixture.
    db_conn.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(extension)))

    row = db_conn.execute("SELECT 1 FROM pg_extension WHERE extname = %s", (extension,)).fetchone()

    assert row is not None
