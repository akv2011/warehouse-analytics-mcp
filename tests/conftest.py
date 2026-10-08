from pathlib import Path

import pytest

from warehouse_mcp import data


@pytest.fixture(scope="session")
def warehouse(tmp_path_factory) -> Path:
    return data.build(tmp_path_factory.mktemp("warehouse") / "sample.duckdb")
