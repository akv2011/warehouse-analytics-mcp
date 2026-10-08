"""The connection must refuse these on its own, so the guard is not the only thing standing in the way."""

import duckdb
import pytest

from warehouse_mcp.server import connect


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE stolen AS SELECT * FROM users",
        "DELETE FROM purchases",
        "COPY users TO '{tmp}/users.csv'",
        "SELECT * FROM read_csv('/etc/hosts')",
        "SELECT * FROM read_text('/etc/hosts')",
        "INSTALL httpfs",
        "SET enable_external_access = true",
        "SET lock_configuration = false",
        "ATTACH '{tmp}/other.duckdb' AS other",
    ],
)
def test_connection_refuses_without_the_guard(warehouse, tmp_path, sql):
    con = connect(warehouse)
    with pytest.raises(duckdb.Error):
        con.execute(sql.format(tmp=tmp_path))
    assert not (tmp_path / "users.csv").exists()
