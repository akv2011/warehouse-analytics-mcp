"""Turn a metric name, a date range, a grain and dimensions into SQL, so a model never writes metric SQL."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

DIMENSIONS = ("country", "platform", "channel")
GRAINS = ("day", "week", "month")
MAX_DAYS = 366


@dataclass(frozen=True)
class Metric:
    name: str
    description: str
    unit: str
    table: str | None = None
    date_column: str | None = None
    expression: str | None = None
    numerator: tuple[str, ...] = ()
    denominator: str | None = None


def load(path: Path = Path(__file__).with_name("metrics.yaml")) -> dict[str, Metric]:
    raw = yaml.safe_load(path.read_text())
    metrics = {
        name: Metric(name=name, **{**spec, "numerator": tuple(spec.get("numerator", ()))})
        for name, spec in raw.items()
    }
    for m in metrics.values():
        for part in (*m.numerator, *([m.denominator] if m.denominator else [])):
            if part not in metrics or metrics[part].expression is None:
                raise ValueError(f"metric {m.name!r} uses {part!r}, which is not a simple metric")
    return metrics


def _simple(m: Metric, grain: str, group_by: tuple[str, ...]) -> str:
    owner = "f" if m.table == "users" else "u"
    join = "" if m.table == "users" else "JOIN users u ON u.user_id = f.user_id"
    dims = "".join(f", {owner}.{d}" for d in group_by)
    return (
        f"SELECT CAST(date_trunc('{grain}', f.{m.date_column}) AS DATE) AS period{dims}, {m.expression} AS value "
        f"FROM {m.table} f {join} "
        f"WHERE CAST(f.{m.date_column} AS DATE) BETWEEN ? AND ? "
        f"GROUP BY ALL"
    )


def build(
    metrics: dict[str, Metric], name: str, start: date, end: date, grain: str, group_by: list[str]
) -> tuple[str, list[date]]:
    """Return (sql, params). Every model-supplied value is checked against a fixed list or bound as a parameter."""
    if name not in metrics:
        raise ValueError(f"unknown metric {name!r}; metrics are {', '.join(metrics)}")
    if grain not in GRAINS:
        raise ValueError(f"grain must be one of {', '.join(GRAINS)}")
    if bad := [d for d in group_by if d not in DIMENSIONS]:
        raise ValueError(f"unknown dimension {bad[0]!r}; dimensions are {', '.join(DIMENSIONS)}")
    if start > end:
        raise ValueError("start_date is after end_date")
    if (end - start).days >= MAX_DAYS:
        raise ValueError(f"ask for at most {MAX_DAYS} days at a time")

    dims = tuple(dict.fromkeys(group_by))
    m = metrics[name]
    if m.expression is not None:
        return _simple(m, grain, dims) + " ORDER BY ALL", [start, end]

    parts = [*m.numerator, m.denominator]
    keys = ", ".join(("period", *dims))
    ctes = ", ".join(f"m{i} AS ({_simple(metrics[p], grain, dims)})" for i, p in enumerate(parts))
    top = " + ".join(f"coalesce(m{i}.value, 0)" for i in range(len(m.numerator)))
    joins = " ".join(f"LEFT JOIN m{i} USING ({keys})" for i in range(len(m.numerator)))
    d = len(parts) - 1
    sql = (
        f"WITH {ctes} "
        f"SELECT {', '.join(f'm{d}.{k}' for k in ('period', *dims))}, ({top}) / m{d}.value AS value "
        f"FROM m{d} {joins} ORDER BY ALL"
    )
    return sql, [start, end] * len(parts)
