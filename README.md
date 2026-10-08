# warehouse-analytics-mcp

An MCP server that lets an agent answer analytics questions over a data warehouse without being able to change it, read files on the host, or run away with a query.

It ships with a generated warehouse for a mobile app (20,000 users over 90 days: installs, daily sessions, purchases, ad revenue), so it runs with no setup. Point `WAREHOUSE_DB` at your own DuckDB file to use real data.

## Tools

| Tool | What it does |
|---|---|
| `list_metrics` | Metrics with fixed definitions: installs, active users, sessions, purchase and ad revenue, paying users, day-1 and day-7 retention, ARPU, payer conversion |
| `query_metric` | A metric by day, week or month over a date range, split by country, platform or channel |
| `list_tables` | Tables with row counts and column types |
| `run_sql` | One read-only SELECT for questions no metric answers |

All four carry `readOnlyHint: true` and return typed results with an output schema. Every query decision is kept in the `audit://queries` resource.

## Why metrics first

When a model writes its own SQL for "revenue last week", two questions can get two different definitions of revenue. `query_metric` takes a metric name, dates, a grain and dimensions, and builds the SQL from `warehouse_mcp/metrics.yaml`. Every value the model supplies is either checked against a fixed list or bound as a query parameter, so there is nothing to inject. Adding a metric is a few lines of YAML; ratio metrics such as ARPU are defined as sums of simple metrics over another.

## Guardrails on run_sql

Three layers, each enough to stop writes on its own:

1. **The connection.** DuckDB opens the file read-only with `enable_external_access` off, extension autoloading off and `lock_configuration` on, so nothing can write, read host files, reach the network or change those settings. `tests/test_lockdown.py` sends `CREATE`, `DELETE`, `COPY ... TO`, `read_csv('/etc/hosts')`, `INSTALL`, `SET` and `ATTACH` straight to the connection, past the guard, and each one fails.
2. **The guard.** sqlglot parses the query: exactly one statement, and it must be a SELECT, UNION or CTE query. No write, DDL, PRAGMA, SET, COPY or ATTACH nodes anywhere, no table functions, no file or settings functions, and only the published tables. Only SQL rebuilt from the parsed tree reaches DuckDB, so comments and anything hidden in them are dropped.
3. **Limits.** Results are capped at `max_rows` (1,000 at most) with a `truncated` flag, and a query is interrupted after 10 seconds.

A refused query comes back with `isError: true` and the reason, for example `query rejected: table functions are not allowed; query the published tables`.

## Run

```sh
uv sync
uv run warehouse-mcp            # stdio
uv run warehouse-mcp --http     # Streamable HTTP on 127.0.0.1:8001/mcp
WAREHOUSE_DB=/path/to/your.duckdb uv run warehouse-mcp
```

The HTTP mode binds to localhost and has no authentication; put it behind your own auth before exposing it.

## Connect a client

Replace `/path/to` with where you cloned this repo.

Claude Code:

```sh
claude mcp add warehouse -- uv --directory /path/to/warehouse-analytics-mcp run warehouse-mcp
```

Codex CLI:

```sh
codex mcp add warehouse -- uv --directory /path/to/warehouse-analytics-mcp run warehouse-mcp
```

Gemini CLI (no `--` before the command):

```sh
gemini mcp add -s user warehouse uv --directory /path/to/warehouse-analytics-mcp run warehouse-mcp
```

Claude Desktop (`claude_desktop_config.json`) and Cursor (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "warehouse": {
      "command": "uv",
      "args": ["--directory", "/path/to/warehouse-analytics-mcp", "run", "warehouse-mcp"]
    }
  }
}
```

MCP Inspector:

```sh
npx @modelcontextprotocol/inspector /path/to/warehouse-analytics-mcp/.venv/bin/warehouse-mcp
```

Checked on 2026-10-08: Claude Code 2.1.294 connects; MCP Inspector lists the four tools, runs `query_metric` and gets `run_sql` refusing `read_csv('/etc/hosts')` with `isError`; Codex CLI 0.156.1 and Gemini CLI 0.63.0 accept the config.

## Tests

```sh
uv run pytest
```

55 tests: the guard against 24 kinds of unsafe query, the connection refusing 9 of them with the guard bypassed, each metric against hand-written SQL, input validation, and the MCP surface (read-only annotations, output schemas, errors with reasons, the row cap, the timeout and the audit trail).

## Layout

```
warehouse_mcp/server.py     MCP server, connection, limits, audit
warehouse_mcp/guard.py      SQL checks for run_sql
warehouse_mcp/metrics.py    metric SQL builder
warehouse_mcp/metrics.yaml  metric definitions
warehouse_mcp/data.py       the generated sample warehouse
```

## License

MIT
