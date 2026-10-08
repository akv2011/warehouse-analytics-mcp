import argparse
import os
import tempfile
import threading
import time
from collections import deque
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
import duckdb
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from warehouse_mcp import data, guard, metrics

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
TIMEOUT_SECONDS = 10.0
MaxRows = Annotated[int, Field(ge=1, le=1000)]

INSTRUCTIONS = """\
Read-only analytics over a mobile app warehouse: installs, sessions, purchases and ad revenue.
Prefer query_metric: metrics are defined once, so numbers agree across questions.
Use run_sql only for questions no metric answers. It accepts one SELECT over the published tables,
returns at most max_rows rows and stops after 10 seconds. Call list_tables for columns."""


class Column(BaseModel):
    name: str
    type: str


class Table(BaseModel):
    name: str
    rows: int
    columns: list[Column]


class MetricInfo(BaseModel):
    name: str
    description: str
    unit: str
    dimensions: list[str]


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = Field(description="True when more rows matched than max_rows")
    sql: str = Field(description="The SQL that ran, for the model or the user to check")


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    """Open the warehouse read-only, with no file, network or extension access, and lock those settings."""
    return duckdb.connect(
        str(path),
        read_only=True,
        config={
            "enable_external_access": False,
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
            "threads": 2,
            "max_memory": "512MB",
            "lock_configuration": True,
        },
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def run(con: duckdb.DuckDBPyConnection, sql: str, params: list, max_rows: int, timeout: float) -> QueryResult:
    cur = con.cursor()
    timer = threading.Timer(timeout, cur.interrupt)
    timer.start()
    try:
        cur.execute(sql, params)
        rows = cur.fetchmany(max_rows + 1)
        columns = [d[0] for d in cur.description]
    except duckdb.InterruptException:
        raise ToolError(f"query stopped after {timeout:g} s; narrow it or use a metric") from None
    except duckdb.Error as e:
        raise ToolError(f"query failed: {str(e).splitlines()[0]}") from None
    finally:
        timer.cancel()
        cur.close()
    return QueryResult(
        columns=columns,
        rows=[[_jsonable(v) for v in row] for row in rows[:max_rows]],
        truncated=len(rows) > max_rows,
        sql=sql,
    )


def build_server(db_path: Path, timeout: float = TIMEOUT_SECONDS) -> FastMCP:
    con = connect(db_path)
    tables = frozenset(r[0].lower() for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall())
    catalog = metrics.load()
    available = {n: m for n, m in catalog.items() if all(
        (catalog[p].table or "") in tables for p in ((n,) if m.expression else (*m.numerator, m.denominator))
    )}
    audit: deque[dict[str, Any]] = deque(maxlen=200)

    def record(tool: str, verdict: str, detail: str) -> None:
        audit.appendleft({"at": datetime.now().isoformat(timespec="seconds"), "tool": tool, "verdict": verdict, "detail": detail})

    mcp = FastMCP("warehouse-analytics", instructions=INSTRUCTIONS)

    @mcp.tool(annotations=READ_ONLY)
    async def list_tables() -> list[Table]:
        """Tables you can query, with row counts and column types."""
        out = []
        for name in sorted(tables):
            cols = con.execute(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position", [name]
            ).fetchall()
            count = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            out.append(Table(name=name, rows=count, columns=[Column(name=c, type=t) for c, t in cols]))
        return out

    @mcp.tool(annotations=READ_ONLY)
    async def list_metrics() -> list[MetricInfo]:
        """Metrics with fixed definitions. Every metric can be split by the same dimensions."""
        return [MetricInfo(name=m.name, description=m.description, unit=m.unit, dimensions=list(metrics.DIMENSIONS)) for m in available.values()]

    @mcp.tool(annotations=READ_ONLY)
    async def query_metric(
        metric: str,
        start_date: date,
        end_date: date,
        grain: Literal["day", "week", "month"] = "day",
        group_by: list[Literal["country", "platform", "channel"]] | None = None,
        max_rows: MaxRows = 500,
    ) -> QueryResult:
        """A metric by day, week or month over an inclusive date range, optionally split by dimensions."""
        try:
            sql, params = metrics.build(available, metric, start_date, end_date, grain, list(group_by or []))
        except ValueError as e:
            record("query_metric", "rejected", str(e))
            raise ToolError(str(e)) from None
        result = await anyio.to_thread.run_sync(run, con, sql, params, max_rows, timeout)
        record("query_metric", "ran", f"{metric} {start_date}..{end_date} by {grain}")
        return result

    @mcp.tool(annotations=READ_ONLY)
    async def run_sql(sql: str, max_rows: MaxRows = 200) -> QueryResult:
        """One read-only SELECT over the published tables, for questions no metric answers."""
        try:
            guarded = guard.check(sql, max_rows, tables)
        except guard.SqlRejected as e:
            record("run_sql", "rejected", str(e))
            raise ToolError(f"query rejected: {e}") from None
        started = time.monotonic()
        result = await anyio.to_thread.run_sync(run, con, guarded, [], max_rows, timeout)
        record("run_sql", "ran", f"{len(result.rows)} rows in {time.monotonic() - started:.2f} s")
        return result

    @mcp.resource("audit://queries", mime_type="application/json")
    async def queries() -> list[dict[str, Any]]:
        """The last 200 query decisions: what ran, what was rejected and why."""
        return list(audit)

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only analytics MCP server")
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP on 127.0.0.1 instead of stdio")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    db = os.getenv("WAREHOUSE_DB")
    path = Path(db) if db else data.build(Path(tempfile.mkdtemp()) / "sample.duckdb")
    server = build_server(path)
    if args.http:
        server.run(transport="http", host="127.0.0.1", port=args.port)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
