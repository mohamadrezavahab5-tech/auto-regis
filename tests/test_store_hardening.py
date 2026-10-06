import sqlite3

import pytest

from autoreview import store


def test_read_connection_is_query_only_and_has_short_busy_timeout(tmp_path):
    path = tmp_path / "db.sqlite"
    db = store.connect(path)
    db.close()

    ro = store.connect_read(path, busy_timeout_ms=123)
    try:
        assert ro.execute("PRAGMA query_only").fetchone()[0] == 1
        assert ro.execute("PRAGMA busy_timeout").fetchone()[0] == 123
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("CREATE TABLE should_not_write(x)")
    finally:
        ro.close()
