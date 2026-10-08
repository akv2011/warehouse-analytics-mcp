"""Decide whether a SQL string from a model may run, and cap what it can return.

The database connection is already read-only with external access off, so this layer exists to give
the model a clear reason for a refusal and to keep queries on the published tables.
"""

import sqlglot
from sqlglot import exp


class SqlRejected(ValueError):
    pass


FORBIDDEN = tuple(
    getattr(exp, name)
    for name in ("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "Command", "Pragma", "Set", "Copy", "Attach", "Detach", "Use", "Transaction", "Commit", "Rollback", "Install", "Load")
    if hasattr(exp, name)
)
FUNCTION_PREFIXES = ("read_", "parquet_", "sniff_", "delta_", "iceberg_", "duckdb_", "pragma_")
FUNCTIONS = {"getenv", "glob", "query", "query_table", "current_setting"}


def _function_name(node: exp.Func) -> str:
    return (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()


def check(sql: str, max_rows: int, tables: frozenset[str]) -> str:
    """Return SQL that is safe to run and asks for at most max_rows + 1 rows, or raise SqlRejected."""
    try:
        # A comment after the last semicolon parses as an empty Semicolon node, not a statement.
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None and not isinstance(s, exp.Semicolon)]
    except sqlglot.errors.ParseError as e:
        raise SqlRejected(f"could not parse the query: {str(e).splitlines()[0]}") from None
    if len(statements) != 1:
        raise SqlRejected("send exactly one statement")
    query = statements[0]
    if not isinstance(query, exp.Query):
        raise SqlRejected("only SELECT queries are allowed")
    if bad := next(query.find_all(*FORBIDDEN), None):
        raise SqlRejected(f"{bad.key.upper()} is not allowed")

    ctes = {cte.alias_or_name.lower() for cte in query.find_all(exp.CTE)}
    for table in query.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise SqlRejected("table functions are not allowed; query the published tables")
        if table.db or table.catalog:
            raise SqlRejected(f"use unqualified table names, not {table.sql(dialect='duckdb')}")
        if table.name.lower() not in tables and table.name.lower() not in ctes:
            raise SqlRejected(f"unknown table {table.name!r}; tables are {', '.join(sorted(tables))}")

    for func in query.find_all(exp.Func):
        name = _function_name(func)
        if name in FUNCTIONS or name.startswith(FUNCTION_PREFIXES):
            raise SqlRejected(f"function {name}() is not allowed")

    return f"SELECT * FROM ({query.sql(dialect='duckdb')}) AS guarded LIMIT {max_rows + 1}"
