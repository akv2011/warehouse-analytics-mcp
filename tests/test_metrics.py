from datetime import date

import pytest

from warehouse_mcp import metrics
from warehouse_mcp.server import connect

CATALOG = metrics.load()
DAY = date(2026, 8, 15)


def one(con, name, **kw):
    sql, params = metrics.build(CATALOG, name, kw.get("start", DAY), kw.get("end", DAY), kw.get("grain", "day"), kw.get("group_by", []))
    return con.execute(sql, params).fetchall()


def test_active_users_match_a_direct_count(warehouse):
    con = connect(warehouse)
    expected = con.execute("SELECT count(DISTINCT user_id) FROM sessions WHERE session_date = ?", [DAY]).fetchone()[0]
    assert one(con, "active_users") == [(DAY, expected)]


def test_arpu_is_purchase_plus_ad_revenue_over_active_users(warehouse):
    con = connect(warehouse)
    iap, ad, active = con.execute(
        "SELECT (SELECT sum(amount_usd) FROM purchases WHERE CAST(purchased_at AS DATE) = $1),"
        " (SELECT sum(revenue_usd) FROM ad_revenue WHERE revenue_date = $1),"
        " (SELECT count(DISTINCT user_id) FROM sessions WHERE session_date = $1)",
        [DAY],
    ).fetchone()
    assert one(con, "arpu")[0][1] == pytest.approx((float(iap) + ad) / active)


def test_d1_retention_matches_a_join(warehouse):
    con = connect(warehouse)
    expected = con.execute(
        "SELECT avg(CASE WHEN s.user_id IS NULL THEN 0 ELSE 1 END) FROM users u"
        " LEFT JOIN sessions s ON s.user_id = u.user_id AND s.session_date = u.install_date + 1 WHERE u.install_date = ?",
        [DAY],
    ).fetchone()[0]
    assert one(con, "d1_retention")[0][1] == pytest.approx(expected)
    assert 0.3 < expected < 0.6


def test_group_by_splits_the_total(warehouse):
    con = connect(warehouse)
    total = one(con, "installs", grain="month", start=date(2026, 8, 1), end=date(2026, 8, 31))[0][1]
    split = one(con, "installs", grain="month", start=date(2026, 8, 1), end=date(2026, 8, 31), group_by=["platform", "channel"])
    assert sum(r[-1] for r in split) == total and len(split) == 10


@pytest.mark.parametrize(
    ("name", "kw", "message"),
    [
        ("revenue_per_dolphin", {}, "unknown metric"),
        ("installs", {"grain": "hour"}, "grain"),
        ("installs", {"group_by": ["country; DROP TABLE users"]}, "unknown dimension"),
        ("installs", {"start": date(2026, 9, 1), "end": date(2026, 8, 1)}, "after"),
        ("installs", {"start": date(2025, 1, 1), "end": date(2026, 9, 1)}, "at most"),
    ],
)
def test_inputs_outside_the_fixed_lists_are_refused(name, kw, message):
    with pytest.raises(ValueError, match=message):
        metrics.build(CATALOG, name, kw.get("start", DAY), kw.get("end", DAY), kw.get("grain", "day"), kw.get("group_by", []))


def test_every_ratio_metric_is_built_from_simple_metrics():
    for m in CATALOG.values():
        if m.expression is None:
            assert m.numerator and m.denominator
