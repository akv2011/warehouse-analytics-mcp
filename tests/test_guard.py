import pytest

from warehouse_mcp.data import TABLES
from warehouse_mcp.guard import SqlRejected, check

ALLOWED = frozenset(TABLES)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO users VALUES (1, DATE '2026-01-01', 'US', 'ios', 'organic')",
        "UPDATE users SET country = 'US'",
        "DELETE FROM purchases",
        "CREATE TABLE stolen AS SELECT * FROM users",
        "DROP TABLE users",
        "ALTER TABLE users ADD COLUMN x INT",
        "SELECT * FROM users; DROP TABLE users",
        "COPY users TO '/tmp/users.csv'",
        "ATTACH 'other.duckdb' AS other",
        "PRAGMA database_list",
        "SET enable_external_access = true",
        "INSTALL httpfs",
        "LOAD httpfs",
        "SELECT * FROM read_csv('/etc/hosts')",
        "SELECT * FROM read_parquet('s3://bucket/x.parquet')",
        "SELECT * FROM glob('/Users/*')",
        "SELECT * FROM duckdb_settings()",
        "SELECT getenv('HOME')",
        "SELECT current_setting('enable_external_access')",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM secrets",
        "SELECT * FROM main.users",
        "",
        "SELEC * FROM users",
    ],
)
def test_rejected(sql):
    with pytest.raises(SqlRejected):
        check(sql, 10, ALLOWED)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT country, count(*) FROM users GROUP BY country",
        "WITH payers AS (SELECT DISTINCT user_id FROM purchases) SELECT count(*) FROM payers JOIN users USING (user_id)",
        "SELECT user_id FROM users UNION SELECT user_id FROM purchases",
        "SELECT user_id, sum(amount_usd) OVER (PARTITION BY user_id) FROM purchases",
        "select * from USERS",
    ],
)
def test_allowed_queries_are_capped(sql):
    assert check(sql, 10, ALLOWED).endswith("LIMIT 11")


def test_a_trailing_comment_is_not_a_second_statement():
    assert check("SELECT 1 FROM users; -- done", 5, ALLOWED).endswith("LIMIT 6")


def test_only_the_parsed_query_reaches_the_database_never_the_original_text():
    assert check("SELECT 1 FROM users; -- x; DROP TABLE users", 5, ALLOWED) == "SELECT * FROM (SELECT 1 FROM users) AS guarded LIMIT 6"
