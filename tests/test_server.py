import json

from fastmcp import Client

from warehouse_mcp.server import build_server


async def test_every_tool_is_read_only_and_typed(warehouse):
    async with Client(build_server(warehouse)) as c:
        tools = await c.list_tools()
    assert {t.name for t in tools} == {"list_tables", "list_metrics", "query_metric", "run_sql"}
    for t in tools:
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False
        assert t.output_schema


async def test_a_rejected_query_is_an_error_with_the_reason_and_is_audited(warehouse):
    async with Client(build_server(warehouse)) as c:
        result = await c.call_tool("run_sql", {"sql": "COPY users TO '/tmp/u.csv'"}, raise_on_error=False)
        audit = json.loads((await c.read_resource("audit://queries"))[0].text)
    assert result.is_error and "only SELECT queries are allowed" in result.content[0].text
    assert audit[0]["tool"] == "run_sql" and audit[0]["verdict"] == "rejected"


async def test_results_are_capped_and_say_so(warehouse):
    async with Client(build_server(warehouse)) as c:
        result = await c.call_tool("run_sql", {"sql": "SELECT user_id FROM users", "max_rows": 5})
    assert len(result.structured_content["rows"]) == 5 and result.structured_content["truncated"] is True


async def test_a_slow_query_is_stopped(warehouse):
    async with Client(build_server(warehouse, timeout=0.2)) as c:
        result = await c.call_tool(
            "run_sql", {"sql": "SELECT count(*) FROM sessions a, sessions b, sessions c"}, raise_on_error=False
        )
    assert result.is_error and "stopped after" in result.content[0].text


async def test_a_metric_query_returns_rows_and_the_sql_it_ran(warehouse):
    async with Client(build_server(warehouse)) as c:
        result = await c.call_tool(
            "query_metric", {"metric": "d7_retention", "start_date": "2026-07-01", "end_date": "2026-08-31", "grain": "month"}
        )
        bad = await c.call_tool("query_metric", {"metric": "nope", "start_date": "2026-07-01", "end_date": "2026-07-02"}, raise_on_error=False)
    rows = result.structured_content["rows"]
    assert [r[0] for r in rows] == ["2026-07-01", "2026-08-01"] and all(0.1 < r[1] < 0.4 for r in rows)
    assert "date_trunc('month'" in result.structured_content["sql"]
    assert bad.is_error and "unknown metric" in bad.content[0].text
